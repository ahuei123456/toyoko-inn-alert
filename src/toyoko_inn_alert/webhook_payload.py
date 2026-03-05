from datetime import UTC, datetime
from typing import Any

from toyoko_inn_alert.db import Watch


def build_webhook_payload(
    *,
    event: str,
    watch: Watch,
    price: int,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    emitted_at = timestamp or datetime.now(UTC)
    stay = {
        "checkin": watch.checkin_date.isoformat(),
        "checkout": watch.checkout_date.isoformat(),
        "people": watch.num_people,
        "smoking": watch.smoking_type,
        "room_type": watch.room_type,
    }
    return {
        "event": event,
        "timestamp": emitted_at.isoformat(),
        "user_id": watch.user_id,
        "hotel": {"code": watch.hotel_code, "price": price},
        "stay": stay,
    }


def add_booking_url_fields(payload: dict[str, Any], booking_url: str) -> dict[str, Any]:
    payload["booking_url"] = booking_url
    return payload
