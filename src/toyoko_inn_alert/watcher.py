import json
import logging
from datetime import UTC, datetime

from sqlmodel import Session, select

from toyoko_inn_alert.client import ToyokoClient
from toyoko_inn_alert.db import Notification, Watch, engine

logger = logging.getLogger("toyoko.watcher")


class Watcher:
    def __init__(self, client: ToyokoClient | None = None):
        self.client = client or ToyokoClient()

    async def run_once(self):
        """
        Executes one full polling cycle across all active watches.
        """
        logger.info("watcher_cycle_start")
        with Session(engine) as session:
            # 1. Fetch all active watches
            statement = select(Watch)
            watches = session.exec(statement).all()
            logger.info("watcher_active_watches count=%d", len(watches))

            if not watches:
                logger.info("watcher_cycle_no_active_watches")
                return

            # 2. Group watches by criteria to batch API calls
            groups: dict[tuple, list[Watch]] = {}
            for w in watches:
                key = (
                    w.checkin_date,
                    w.checkout_date,
                    w.num_people,
                    w.smoking_type,
                )
                if key not in groups:
                    groups[key] = []
                groups[key].append(w)
            logger.info("watcher_group_count count=%d", len(groups))

            processed_groups = 0
            processed_batches = 0
            hit_count = 0

            for key, group_watches in groups.items():
                processed_groups += 1
                checkin, checkout, people, smoking = key

                # Toyoko API supports up to 10 hotels per batch
                hotel_codes = list(set(w.hotel_code for w in group_watches))
                # Chunks of 10
                for i in range(0, len(hotel_codes), 10):
                    processed_batches += 1
                    batch_codes = hotel_codes[i : i + 10]
                    try:
                        results = await self.client.fetch_prices(
                            batch_codes, checkin, checkout, people, 1, smoking
                        )

                        # 3. Process results and detect state transitions
                        for w in group_watches:
                            if w.hotel_code in results.prices:
                                status = results.prices[w.hotel_code]
                                is_available = status.existEnoughVacantRooms

                                # HIT: Was false, now true
                                if not w.last_available and is_available:
                                    hit_count += 1
                                    logger.info(
                                        "watcher_hit watch_id=%s user_id=%s "
                                        "hotel_code=%s price=%d",
                                        w.id,
                                        w.user_id,
                                        w.hotel_code,
                                        status.lowestPrice,
                                    )
                                    self._create_notification(
                                        session, w, status.lowestPrice
                                    )

                                w.last_available = is_available

                        session.commit()
                    except Exception as e:
                        logger.exception(
                            "watcher_group_poll_failed key=%s error=%s", key, e
                        )

        logger.info(
            "watcher_cycle_complete groups=%d batches=%d hits=%d",
            processed_groups,
            processed_batches,
            hit_count,
        )

    def _create_notification(self, session: Session, watch: Watch, price: int):
        payload = {
            "event": "AVAILABILITY_FOUND",
            "timestamp": datetime.now(UTC).isoformat(),
            "userId": watch.user_id,
            "hotel": {"code": watch.hotel_code, "price": price},
            "stay": {
                "checkin": watch.checkin_date.isoformat(),
                "checkout": watch.checkout_date.isoformat(),
            },
        }
        notification = Notification(watch_id=watch.id, payload=json.dumps(payload))
        session.add(notification)
        logger.info(
            "watcher_notification_queued watch_id=%s user_id=%s hotel_code=%s",
            watch.id,
            watch.user_id,
            watch.hotel_code,
        )
