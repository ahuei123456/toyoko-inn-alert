import json
import logging
import os
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Annotated, Any

from anyio import to_thread
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from toyoko_inn_alert.client import ToyokoClient
from toyoko_inn_alert.data_loader import load_hotels
from toyoko_inn_alert.db import (
    APIKey,
    Notification,
    Watch,
    create_db_and_tables,
    get_session,
)
from toyoko_inn_alert.notifier import Notifier
from toyoko_inn_alert.watcher import Watcher
from toyoko_inn_alert.webhook_payload import build_webhook_payload

# Load hotels once for validation
HOTELS = load_hotels("data/hotels.json")

# Polling intervals
POLLING_INTERVAL_SECONDS = 3600
QUEUE_INTERVAL_SECONDS = 60

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")

security = HTTPBasic()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates")
)
logger = logging.getLogger("toyoko.api")


def error_detail(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _begin_immediate(session: Session) -> None:
    # SQLite needs an immediate write transaction so the count + insert path
    # is serialized across concurrent API workers.
    session.connection().exec_driver_sql("BEGIN IMMEDIATE")


def _create_watch_record(bind: Any, watch: Watch) -> int:
    with Session(bind=bind) as write_session:
        _begin_immediate(write_session)
        try:
            existing = write_session.exec(
                select(Watch).where(
                    Watch.hotel_code == watch.hotel_code,
                    Watch.checkin_date == watch.checkin_date,
                    Watch.checkout_date == watch.checkout_date,
                    Watch.user_id == watch.user_id,
                )
            ).first()
            if existing:
                logger.info(
                    "watch_create_rejected_duplicate user_id=%s hotel_code=%s "
                    "watch_id=%s",
                    watch.user_id,
                    watch.hotel_code,
                    existing.id,
                )
                raise HTTPException(
                    status_code=409,
                    detail=error_detail(
                        "DUPLICATE_WATCH",
                        "Watch already exists for this user and date",
                    ),
                )

            active_watches_count = write_session.exec(
                select(func.count())
                .select_from(Watch)
                .where(Watch.user_id == watch.user_id)
            ).one()
            if active_watches_count >= 10:
                logger.warning(
                    "watch_create_rejected_max_watches user_id=%s count=%d",
                    watch.user_id,
                    active_watches_count,
                )
                raise HTTPException(
                    status_code=409,
                    detail=error_detail(
                        "MAX_ACTIVE_WATCHES",
                        "You can only have up to 10 active watches.",
                    ),
                )

            write_session.add(watch)
            write_session.commit()
            write_session.refresh(watch)
            if watch.id is None:
                raise RuntimeError("Watch insert completed without an ID")
            return watch.id
        except HTTPException:
            write_session.rollback()
            raise
        except IntegrityError:
            write_session.rollback()
            logger.info(
                "watch_create_rejected_duplicate_db user_id=%s hotel_code=%s",
                watch.user_id,
                watch.hotel_code,
            )
            raise HTTPException(
                status_code=409,
                detail=error_detail(
                    "DUPLICATE_WATCH",
                    "Watch already exists for this user and date",
                ),
            ) from None
        except Exception:
            write_session.rollback()
            raise


def _load_watch(bind: Any, watch_id: int) -> Watch:
    with Session(bind=bind) as read_session:
        watch = read_session.get(Watch, watch_id)
        if watch is None:
            raise RuntimeError(f"Watch {watch_id} disappeared during request")
        return watch


def _queue_instant_hit_notification(
    bind: Any,
    watch_id: int,
    payload: dict[str, Any],
) -> None:
    with Session(bind=bind) as update_session:
        persisted_watch = update_session.get(Watch, watch_id)
        if persisted_watch is None:
            logger.warning(
                "watch_create_instant_hit_watch_missing watch_id=%s",
                watch_id,
            )
            return

        persisted_watch.last_available = True
        notification = Notification(
            watch_id=persisted_watch.id,
            payload=json.dumps(payload),
        )
        update_session.add(persisted_watch)
        update_session.add(notification)
        update_session.commit()


def verify_api_key(
    session: Annotated[Session, Depends(get_session)],
    x_api_key: Annotated[str | None, Header()] = None,
):
    if not x_api_key:
        logger.warning("api_key_auth_missing")
        raise HTTPException(
            status_code=401,
            detail=error_detail("INVALID_API_KEY", "Missing or invalid API key"),
        )

    statement = select(APIKey).where(APIKey.key == x_api_key, APIKey.is_active)
    db_key = session.exec(statement).first()
    if not db_key:
        logger.warning("api_key_auth_failed")
        raise HTTPException(
            status_code=401,
            detail=error_detail("INVALID_API_KEY", "Missing or invalid API key"),
        )
    return db_key


def verify_admin(
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    username_ok = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    password_ok = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (username_ok and password_ok):
        logger.warning("admin_auth_failed username=%s", credentials.username)
        raise HTTPException(
            status_code=401,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Initialize DB
    logger.info("app_startup initializing_database")
    create_db_and_tables()

    # 2. Setup Scheduler
    scheduler = AsyncIOScheduler()
    watcher = Watcher()
    notifier = Notifier()

    scheduler.add_job(
        watcher.run_once,
        "interval",
        seconds=POLLING_INTERVAL_SECONDS,
        id="watcher_job",
    )
    scheduler.add_job(
        notifier.process_queue,
        "interval",
        seconds=QUEUE_INTERVAL_SECONDS,
        id="notifier_job",
    )

    scheduler.start()
    app.state.scheduler = scheduler
    logger.info(
        "app_startup scheduler_started watcher_interval_seconds=%d "
        "queue_interval_seconds=%d",
        POLLING_INTERVAL_SECONDS,
        QUEUE_INTERVAL_SECONDS,
    )

    yield

    # 3. Shutdown
    logger.info("app_shutdown scheduler_stopping")
    scheduler.shutdown()
    logger.info("app_shutdown scheduler_stopped")


app = FastAPI(title="Toyoko Inn Alert API", lifespan=lifespan)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id

    client_ip = request.client.host if request.client else "-"
    start = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = int((perf_counter() - start) * 1000)
        logger.exception(
            "http_request_error request_id=%s method=%s path=%s "
            "duration_ms=%d client_ip=%s",
            request_id,
            request.method,
            request.url.path,
            duration_ms,
            client_ip,
        )
        raise

    duration_ms = int((perf_counter() - start) * 1000)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "http_request request_id=%s method=%s path=%s status=%d "
        "duration_ms=%d client_ip=%s",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        client_ip,
    )
    return response


@app.post("/watches", response_model=Watch)
async def create_watch(
    watch: Watch,
    session: Annotated[Session, Depends(get_session)],
    _: Annotated[APIKey, Depends(verify_api_key)],
):
    logger.info(
        "watch_create_requested user_id=%s hotel_code=%s",
        watch.user_id,
        watch.hotel_code,
    )

    # Ensure dates are datetime objects
    if isinstance(watch.checkin_date, str):
        watch.checkin_date = datetime.fromisoformat(watch.checkin_date)
    if isinstance(watch.checkout_date, str):
        watch.checkout_date = datetime.fromisoformat(watch.checkout_date)

    if not watch.created_at:
        watch.created_at = datetime.now(UTC)

    # 1. Validation
    if watch.hotel_code not in HOTELS:
        logger.warning(
            "watch_create_rejected_invalid_hotel user_id=%s hotel_code=%s",
            watch.user_id,
            watch.hotel_code,
        )
        raise HTTPException(
            status_code=400,
            detail=error_detail("INVALID_HOTEL_CODE", "Invalid hotel code"),
        )

    if watch.checkin_date >= watch.checkout_date:
        logger.warning(
            "watch_create_rejected_invalid_dates user_id=%s checkin=%s checkout=%s",
            watch.user_id,
            watch.checkin_date.isoformat(),
            watch.checkout_date.isoformat(),
        )
        raise HTTPException(
            status_code=400,
            detail=error_detail(
                "INVALID_DATE_RANGE", "Check-in must be before check-out"
            ),
        )

    bind = session.get_bind()
    watch_id = await to_thread.run_sync(_create_watch_record, bind, watch)

    # 2. Instant Hit Check runs after the initial insert is committed so the
    # SQLite write lock is not held while the external HTTP request is in flight.
    client = ToyokoClient()
    try:
        price_result = await client.fetch_prices(
            [watch.hotel_code],
            watch.checkin_date,
            watch.checkout_date,
            num_people=watch.num_people,
            smoking_type=watch.smoking_type,
        )
        status = price_result.prices.get(watch.hotel_code)
        if status and status.existEnoughVacantRooms:
            logger.info(
                "watch_create_instant_hit user_id=%s hotel_code=%s price=%d",
                watch.user_id,
                watch.hotel_code,
                status.lowestPrice,
            )
            payload = build_webhook_payload(
                event="INSTANT_HIT",
                watch=watch,
                price=status.lowestPrice,
            )
            await to_thread.run_sync(
                _queue_instant_hit_notification,
                bind,
                watch_id,
                payload,
            )
        else:
            logger.info(
                "watch_create_instant_hit_not_found user_id=%s hotel_code=%s",
                watch.user_id,
                watch.hotel_code,
            )
    except Exception as e:
        logger.exception(
            "watch_create_instant_hit_failed user_id=%s hotel_code=%s error=%s",
            watch.user_id,
            watch.hotel_code,
            e,
        )

    persisted_watch = await to_thread.run_sync(_load_watch, bind, watch_id)
    logger.info(
        "watch_create_completed watch_id=%s user_id=%s hotel_code=%s last_available=%s",
        persisted_watch.id,
        persisted_watch.user_id,
        persisted_watch.hotel_code,
        persisted_watch.last_available,
    )
    return persisted_watch


@app.get("/watches/{user_id}", response_model=list[Watch])
def list_watches(
    user_id: str,
    session: Annotated[Session, Depends(get_session)],
    _: Annotated[APIKey, Depends(verify_api_key)],
):
    watches = session.exec(select(Watch).where(Watch.user_id == user_id)).all()
    logger.info("watch_list user_id=%s count=%d", user_id, len(watches))
    return watches


@app.delete("/watches/{watch_id}")
def delete_watch(
    watch_id: int,
    session: Annotated[Session, Depends(get_session)],
    _: Annotated[APIKey, Depends(verify_api_key)],
):
    watch = session.get(Watch, watch_id)
    if not watch:
        logger.warning("watch_delete_not_found watch_id=%d", watch_id)
        raise HTTPException(
            status_code=404,
            detail=error_detail("WATCH_NOT_FOUND", "Watch not found"),
        )
    logger.info(
        "watch_delete_requested watch_id=%d user_id=%s hotel_code=%s",
        watch.id,
        watch.user_id,
        watch.hotel_code,
    )
    session.delete(watch)
    session.commit()
    logger.info("watch_delete_completed watch_id=%d", watch_id)
    return {"ok": True}


@app.get("/status")
def get_status():
    return {"status": "healthy", "timestamp": datetime.now(UTC)}


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    _: str = Depends(verify_admin),
):
    watch_count = session.exec(select(func.count()).select_from(Watch)).one()
    pending_count = session.exec(
        select(func.count())
        .select_from(Notification)
        .where(Notification.status == "pending")
    ).one()
    failed_count = session.exec(
        select(func.count())
        .select_from(Notification)
        .where(Notification.status == "failed")
    ).one()
    key_count = session.exec(select(func.count()).select_from(APIKey)).one()

    context = {
        "request": request,
        "watch_count": watch_count,
        "pending_count": pending_count,
        "failed_count": failed_count,
        "key_count": key_count,
    }
    return templates.TemplateResponse(request, "admin_dashboard.html", context)


@app.get("/admin/api-keys", response_class=HTMLResponse)
def admin_api_keys(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    created_key: str | None = None,
    _: str = Depends(verify_admin),
):
    keys = session.exec(select(APIKey).order_by(desc(col(APIKey.created_at)))).all()
    context = {
        "request": request,
        "keys": keys,
        "created_key": created_key,
    }
    return templates.TemplateResponse(request, "admin_api_keys.html", context)


@app.post("/admin/api-keys")
def admin_create_api_key(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    client_name: Annotated[str, Form()],
    _: str = Depends(verify_admin),
):
    cleaned_name = client_name.strip()
    if not cleaned_name:
        raise HTTPException(status_code=400, detail="client_name is required")

    raw_key = f"tk_{secrets.token_urlsafe(32)}"
    new_key = APIKey(key=raw_key, client_name=cleaned_name)
    session.add(new_key)
    session.commit()
    logger.info(
        "admin_api_key_created key_id=%s client_name=%s",
        new_key.id,
        cleaned_name,
    )

    redirect_url = request.url_for("admin_api_keys").include_query_params(
        created_key=raw_key
    )
    return RedirectResponse(url=str(redirect_url), status_code=303)


@app.post("/admin/api-keys/{key_id}/toggle")
def admin_toggle_api_key(
    key_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    _: str = Depends(verify_admin),
):
    key = session.get(APIKey, key_id)
    if not key:
        raise HTTPException(status_code=404, detail="API key not found")

    key.is_active = not key.is_active
    session.add(key)
    session.commit()
    logger.info(
        "admin_api_key_toggled key_id=%d client_name=%s is_active=%s",
        key.id,
        key.client_name,
        key.is_active,
    )

    return RedirectResponse(url=str(request.url_for("admin_api_keys")), status_code=303)


@app.get("/admin/notifications", response_class=HTMLResponse)
def admin_notifications(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    status: str | None = None,
    limit: int = 100,
    _: str = Depends(verify_admin),
):
    safe_limit = max(1, min(limit, 500))

    statement = select(Notification).order_by(desc(col(Notification.created_at)))
    if status in {"pending", "sent", "failed"}:
        statement = statement.where(Notification.status == status)

    notifications = session.exec(statement.limit(safe_limit)).all()
    rows = []
    for notification in notifications:
        watch = session.get(Watch, notification.watch_id)
        rows.append(
            {
                "notification": notification,
                "watch": watch,
            }
        )

    context = {
        "request": request,
        "rows": rows,
        "status_filter": status,
        "limit": safe_limit,
    }
    return templates.TemplateResponse(request, "admin_notifications.html", context)


@app.post("/admin/notifications/{notification_id}/retry")
def admin_retry_notification(
    notification_id: int,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    status_filter: Annotated[str | None, Form()] = None,
    _: str = Depends(verify_admin),
):
    notification = session.get(Notification, notification_id)
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    notification.status = "pending"
    notification.retry_count = 0
    notification.last_retry = None
    session.add(notification)
    session.commit()
    logger.info(
        "admin_notification_retry notification_id=%d watch_id=%d",
        notification.id,
        notification.watch_id,
    )

    redirect_url = request.url_for("admin_notifications")
    if status_filter in {"pending", "sent", "failed"}:
        redirect_url = redirect_url.include_query_params(status=status_filter)
    return RedirectResponse(url=str(redirect_url), status_code=303)
