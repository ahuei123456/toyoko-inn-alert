import hashlib
import hmac
import json
from datetime import UTC, datetime

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


@pytest.mark.asyncio
async def test_notifier_webhook_signature(session: Session, monkeypatch):
    secret = "super-secret-key"
    monkeypatch.setenv("WEBHOOK_SIGNATURE_SECRET", secret)

    notifier = Notifier()

    checkin = datetime.now(UTC)
    checkout = datetime.now(UTC)
    watch = Watch(
        hotel_code="00088",
        checkin_date=checkin,
        checkout_date=checkout,
        user_id="user123",
        callback_url="https://example.com/callback",
    )
    session.add(watch)
    session.commit()

    payload_dict = {
        "event": "AVAILABILITY_FOUND",
        "timestamp": datetime.now(UTC).isoformat(),
        "userId": "user123",
        "hotel": {"code": "00088", "price": 5000},
        "stay": {
            "checkin": checkin.isoformat(),
            "checkout": checkout.isoformat(),
            "people": 1,
            "smoking": "noSmoking",
            "roomType": 10,
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
        signature = request.headers.get("X-Toyoko-Signature")

        # Verify the signature
        payload_with_url = payload_dict.copy()
        payload_with_url["bookingUrl"] = notifier._generate_booking_url(watch)
        raw_payload = json.dumps(payload_with_url, separators=(",", ":")).encode(
            "utf-8"
        )
        expected_signature = hmac.new(
            secret.encode("utf-8"), raw_payload, hashlib.sha256
        ).hexdigest()

        assert signature == expected_signature
        assert request.content == raw_payload
