import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func
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


def verify_api_key(
    x_api_key: Annotated[str, Header()],
    session: Annotated[Session, Depends(get_session)],
):
    statement = select(APIKey).where(APIKey.key == x_api_key, APIKey.is_active)
    db_key = session.exec(statement).first()
    if not db_key:
        raise HTTPException(status_code=401, detail="Invalid or revoked API Key")
    return db_key


def verify_admin(
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    username_ok = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    password_ok = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=401,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Initialize DB
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

    yield

    # 3. Shutdown
    scheduler.shutdown()


app = FastAPI(title="Toyoko Inn Alert API", lifespan=lifespan)


@app.post("/watches", response_model=Watch)
async def create_watch(
    watch: Watch,
    session: Annotated[Session, Depends(get_session)],
    _: Annotated[APIKey, Depends(verify_api_key)],
):
    # Ensure dates are datetime objects
    if isinstance(watch.checkin_date, str):
        watch.checkin_date = datetime.fromisoformat(watch.checkin_date)
    if isinstance(watch.checkout_date, str):
        watch.checkout_date = datetime.fromisoformat(watch.checkout_date)

    if not watch.created_at:
        watch.created_at = datetime.now(UTC)

    # 1. Validation
    if watch.hotel_code not in HOTELS:
        raise HTTPException(status_code=400, detail="Invalid hotel code")

    if watch.checkin_date >= watch.checkout_date:
        raise HTTPException(status_code=400, detail="Check-in must be before check-out")

    # 2. Deduplication check
    existing = session.exec(
        select(Watch).where(
            Watch.hotel_code == watch.hotel_code,
            Watch.checkin_date == watch.checkin_date,
            Watch.checkout_date == watch.checkout_date,
            Watch.user_id == watch.user_id,
        )
    ).first()
    if existing:
        raise HTTPException(
            status_code=409, detail="Watch already exists for this user and date"
        )

    # 3. Instant Hit Check
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
            watch.last_available = True

            # 4. Immediate Notification Queue
            payload = {
                "event": "INSTANT_HIT",
                "timestamp": datetime.now(UTC).isoformat(),
                "userId": watch.user_id,
                "hotel": {"code": watch.hotel_code, "price": status.lowestPrice},
                "stay": {
                    "checkin": watch.checkin_date.isoformat(),
                    "checkout": watch.checkout_date.isoformat(),
                },
            }
            # We need to save the watch first to get an ID
            session.add(watch)
            session.flush()  # Ensure watch.id is generated

            notification = Notification(watch_id=watch.id, payload=json.dumps(payload))
            session.add(notification)
            session.commit()
            session.refresh(watch)
            return watch
    except Exception as e:
        print(f"Instant hit check failed: {e}")

    # Not an instant hit or check failed
    session.add(watch)
    session.commit()
    session.refresh(watch)
    return watch


@app.get("/watches/{user_id}", response_model=list[Watch])
def list_watches(
    user_id: str,
    session: Annotated[Session, Depends(get_session)],
    _: Annotated[APIKey, Depends(verify_api_key)],
):
    watches = session.exec(select(Watch).where(Watch.user_id == user_id)).all()
    return watches


@app.delete("/watches/{watch_id}")
def delete_watch(
    watch_id: int,
    session: Annotated[Session, Depends(get_session)],
    _: Annotated[APIKey, Depends(verify_api_key)],
):
    watch = session.get(Watch, watch_id)
    if not watch:
        raise HTTPException(status_code=404, detail="Watch not found")
    session.delete(watch)
    session.commit()
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

    redirect_url = request.url_for("admin_notifications")
    if status_filter in {"pending", "sent", "failed"}:
        redirect_url = redirect_url.include_query_params(status=status_filter)
    return RedirectResponse(url=str(redirect_url), status_code=303)
