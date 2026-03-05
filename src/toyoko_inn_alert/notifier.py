import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlmodel import Session, select

from toyoko_inn_alert.db import Notification, Watch, engine

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    async def process_queue(self):
        """
        Fetches pending notifications and attempts to deliver them.
        """
        logger.info("Processing notification queue...")
        with Session(engine) as session:
            statement = select(Notification).where(Notification.status == "pending")
            notifications = session.exec(statement).all()

            if not notifications:
                logger.info("No pending notifications.")
                return

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                tasks = [self._deliver(session, n, client) for n in notifications]
                await asyncio.gather(*tasks)

            session.commit()
        logger.info("Queue processing complete.")

    async def _deliver(
        self,
        session: Session,
        notification: Notification,
        client: httpx.AsyncClient,
    ):
        # 1. Fetch the associated watch to get the callback_url
        watch = session.get(Watch, notification.watch_id)
        if not watch:
            logger.error(
                f"Watch {notification.watch_id} not found "
                f"for notification {notification.id}"
            )
            notification.status = "failed"
            return

        # 2. Exponential Backoff Check
        if notification.retry_count > 0 and notification.last_retry:
            # Simple backoff: 2^retry_count minutes
            wait_min = 2**notification.retry_count
            next_retry = notification.last_retry + timedelta(minutes=wait_min)
            if datetime.now(UTC) < next_retry:
                return

        # 3. Attempt POST
        try:
            payload = json.loads(notification.payload)
            # Add booking URL helper
            payload["bookingUrl"] = self._generate_booking_url(watch)

            response = await client.post(watch.callback_url, json=payload)
            response.raise_for_status()

            notification.status = "sent"
            logger.info(
                f"Notification {notification.id} delivered to {watch.callback_url}"
            )
        except Exception as e:
            notification.retry_count += 1
            notification.last_retry = datetime.now(UTC)
            logger.error(
                f"Delivery failed for notification {notification.id} "
                f"(Attempt {notification.retry_count}): {e}"
            )

            if notification.retry_count >= 10:  # Max retries
                notification.status = "failed"

    def _generate_booking_url(self, watch: Watch) -> str:
        base = "https://www.toyoko-inn.com/search/result/room_plan/"
        params = {
            "hotel": watch.hotel_code,
            "start": watch.checkin_date.strftime("%Y-%m-%d"),
            "end": watch.checkout_date.strftime("%Y-%m-%d"),
            "room": 1,
            "people": watch.num_people,
            "smoking": watch.smoking_type,
            "roomType": watch.room_type,
        }
        # Simplified query string builder
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{base}?{query}"
