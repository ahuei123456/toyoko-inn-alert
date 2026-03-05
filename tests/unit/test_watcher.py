from datetime import datetime, timedelta

import httpx
import pytest
import respx
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from toyoko_inn_alert.db import Notification, Watch
from toyoko_inn_alert.watcher import Watcher


@pytest.fixture(name="session")
def session_fixture(mocker):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    # Patch the global engine in watcher.py
    mocker.patch("toyoko_inn_alert.watcher.engine", engine)
    with Session(engine) as session:
        yield session


@pytest.mark.asyncio
async def test_watcher_hit_detection(session: Session):
    # 1. Setup a watch that is currently 'False' (Sold Out)
    checkin = datetime.now() + timedelta(days=5)
    checkout = checkin + timedelta(days=1)
    watch = Watch(
        hotel_code="00088",
        checkin_date=checkin,
        checkout_date=checkout,
        user_id="user1",
        callback_url="http://cb",
        last_available=False,
    )
    session.add(watch)
    session.commit()

    # 2. Mock API to return 'True' (Available)
    mock_response = [
        {
            "result": {
                "data": {
                    "json": {
                        "prices": {
                            "00088": {
                                "lowestPrice": 6000,
                                "existEnoughVacantRooms": True,
                                "isUnderMaintenance": False,
                            }
                        }
                    }
                }
            }
        }
    ]

    with respx.mock:
        respx.get(url__regex=r".*hotels\.availabilities\.prices.*").mock(
            return_value=httpx.Response(200, json=mock_response)
        )

        watcher = Watcher()
        await watcher.run_once()

        # 3. Verify hit detected and notification queued
        session.refresh(watch)
        assert watch.last_available is True

        notifications = session.exec(select(Notification)).all()
        assert len(notifications) == 1
        assert "AVAILABILITY_FOUND" in notifications[0].payload
