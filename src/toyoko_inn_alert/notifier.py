import asyncio
import hashlib
import hmac
import json
import logging
import os
from collections import Counter
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
from sqlmodel import Session, select

from toyoko_inn_alert.db import Notification, Watch, engine
from toyoko_inn_alert.webhook_payload import add_booking_url_fields

logger = logging.getLogger("toyoko.notifier")


class Notifier:
    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        secret = os.getenv("WEBHOOK_SIGNATURE_SECRET")
        if not secret:
            raise RuntimeError("WEBHOOK_SIGNATURE_SECRET is required.")
        self.secret: str = secret

    async def process_queue(self):
        """
        Fetches pending notifications and attempts to deliver them.
        """
        logger.info("notifier_queue_processing_start")
        with Session(engine) as session:
            statement = select(Notification).where(Notification.status == "pending")
            notifications = session.exec(statement).all()
            logger.info("notifier_pending_count count=%d", len(notifications))

            if not notifications:
                logger.info("notifier_queue_empty")
                return

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                tasks = [self._deliver(session, n, client) for n in notifications]
                outcomes = await asyncio.gather(*tasks)

            session.commit()
        counts = Counter(outcomes)
        logger.info(
            "notifier_queue_processing_complete sent=%d failed=%d deferred=%d "
            "missing_watch=%d",
            counts.get("sent", 0),
            counts.get("failed", 0),
            counts.get("deferred", 0),
            counts.get("missing_watch", 0),
        )

    async def _deliver(
        self,
        session: Session,
        notification: Notification,
        client: httpx.AsyncClient,
    ) -> str:
        # 1. Fetch the associated watch to get the callback_url
        watch = session.get(Watch, notification.watch_id)
        if not watch:
            logger.error(
                "notifier_missing_watch notification_id=%s watch_id=%s",
                notification.id,
                notification.watch_id,
            )
            notification.status = "failed"
            return "missing_watch"

        # 2. Exponential Backoff Check
        if notification.retry_count > 0 and notification.last_retry:
            # Simple backoff: 2^retry_count minutes
            wait_min = 2**notification.retry_count
            next_retry = notification.last_retry + timedelta(minutes=wait_min)
            if datetime.now(UTC) < next_retry:
                logger.info(
                    "notifier_backoff_skip notification_id=%s retry_count=%d "
                    "next_retry=%s",
                    notification.id,
                    notification.retry_count,
                    next_retry.isoformat(),
                )
                return "deferred"

        # 3. Attempt POST
        try:
            payload = json.loads(notification.payload)
            payload = add_booking_url_fields(payload, self._generate_booking_url(watch))

            headers = {"Content-Type": "application/json"}
            raw_payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            signature = hmac.new(
                self.secret.encode("utf-8"), raw_payload, hashlib.sha256
            ).hexdigest()
            headers["X-Toyoko-Signature"] = signature
            response = await client.post(
                watch.callback_url, content=raw_payload, headers=headers
            )

            response.raise_for_status()

            notification.status = "sent"
            logger.info(
                "notifier_delivery_sent notification_id=%s watch_id=%s "
                "status_code=%d callback_target=%s",
                notification.id,
                watch.id,
                response.status_code,
                self._safe_callback_target(watch.callback_url),
            )
            return "sent"
        except Exception as e:
            notification.retry_count += 1
            notification.last_retry = datetime.now(UTC)
            logger.warning(
                "notifier_delivery_failed notification_id=%s watch_id=%s "
                "retry_count=%d error=%s",
                notification.id,
                watch.id,
                notification.retry_count,
                e,
            )

            if notification.retry_count >= 10:  # Max retries
                notification.status = "failed"
                logger.error(
                    "notifier_delivery_marked_failed notification_id=%s watch_id=%s",
                    notification.id,
                    watch.id,
                )
            return "failed"

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

    def _safe_callback_target(self, callback_url: str) -> str:
        parts = urlsplit(callback_url)
        return f"{parts.scheme}://{parts.netloc}{parts.path}"
