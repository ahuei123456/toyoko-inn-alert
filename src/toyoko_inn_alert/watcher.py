import json
import logging
from datetime import UTC, datetime

from sqlmodel import Session, select

from toyoko_inn_alert.client import ToyokoClient
from toyoko_inn_alert.db import Notification, Watch, engine

logger = logging.getLogger(__name__)


class Watcher:
    def __init__(self, client: ToyokoClient | None = None):
        self.client = client or ToyokoClient()

    async def run_once(self):
        """
        Executes one full polling cycle across all active watches.
        """
        logger.info("Starting polling cycle...")
        with Session(engine) as session:
            # 1. Fetch all active watches
            statement = select(Watch)
            watches = session.exec(statement).all()

            if not watches:
                logger.info("No active watches found.")
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

            for key, group_watches in groups.items():
                checkin, checkout, people, smoking = key

                # Toyoko API supports up to 10 hotels per batch
                hotel_codes = list(set(w.hotel_code for w in group_watches))
                # Chunks of 10
                for i in range(0, len(hotel_codes), 10):
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
                                    logger.info(
                                        f"HIT! Hotel {w.hotel_code} is available."
                                    )
                                    self._create_notification(
                                        session, w, status.lowestPrice
                                    )

                                w.last_available = is_available

                        session.commit()
                    except Exception as e:
                        logger.error(f"Error polling group {key}: {e}")

        logger.info("Polling cycle complete.")

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
