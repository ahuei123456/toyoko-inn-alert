import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from toyoko_inn_alert.db import Notification, Watch
from toyoko_inn_alert.notifier import Notifier


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _seed_watch(session: Session) -> Watch:
    checkin = datetime.now(UTC)
    checkout = checkin + timedelta(days=1)
    watch = Watch(
        hotel_code="00088",
        checkin_date=checkin,
        checkout_date=checkout,
        user_id="user123",
        callback_url="https://example.com/callback",
    )
    session.add(watch)
    session.commit()
    session.refresh(watch)
    return watch


@pytest.mark.asyncio
async def test_notifier_webhook_signature(session: Session, monkeypatch):
    secret = "super-secret-key"
    monkeypatch.setenv("WEBHOOK_SIGNATURE_SECRET", secret)

    notifier = Notifier()
    watch = _seed_watch(session)
    payload_dict = {
        "event": "AVAILABILITY_FOUND",
        "timestamp": datetime.now(UTC).isoformat(),
        "user_id": watch.user_id,
        "hotel": {"code": watch.hotel_code, "price": 5000},
        "stay": {
            "checkin": watch.checkin_date.isoformat(),
            "checkout": watch.checkout_date.isoformat(),
            "people": 1,
            "smoking": "noSmoking",
            "room_type": 10,
        },
    }

    notification = Notification(watch_id=watch.id, payload=json.dumps(payload_dict))
    session.add(notification)
    session.commit()

    with respx.mock:
        route = respx.post("https://example.com/callback").mock(
            return_value=httpx.Response(200)
        )

        async with httpx.AsyncClient() as client:
            result = await notifier._deliver(session, notification, client)

    assert result == "sent"
    assert route.called

    request = route.calls.last.request
    payload = json.loads(request.content.decode("utf-8"))
    assert payload["user_id"] == watch.user_id
    assert payload["stay"]["room_type"] == watch.room_type
    assert payload["booking_url"] == notifier._generate_booking_url(watch)

    signature = request.headers.get("X-Toyoko-Signature")
    expected_signature = hmac.new(
        secret.encode("utf-8"), request.content, hashlib.sha256
    ).hexdigest()
    assert signature == expected_signature


def test_notifier_requires_secret(monkeypatch):
    monkeypatch.delenv("WEBHOOK_SIGNATURE_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="WEBHOOK_SIGNATURE_SECRET is required"):
        Notifier()
