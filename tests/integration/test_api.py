import base64
from datetime import datetime, timedelta

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from toyoko_inn_alert.api import app, get_session
from toyoko_inn_alert.db import APIKey, Watch


# Setup in-memory database for testing
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


@pytest.fixture(name="api_key")
def api_key_fixture(session: Session):
    key = APIKey(key="test_key", client_name="Test Client")
    session.add(key)
    session.commit()
    return "test_key"


@pytest.fixture(name="client")
def client_fixture(session: Session):
    def get_session_override():
        return session

    app.dependency_overrides[get_session] = get_session_override
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def test_create_watch_success(client: TestClient, api_key: str):
    # Mock the instant hit check (it should return sold out)
    mock_inner = {
        "prices": {
            "00088": {
                "lowestPrice": 0,
                "existEnoughVacantRooms": False,
                "isUnderMaintenance": False,
            }
        }
    }
    mock_response = [{"result": {"data": {"json": mock_inner}}}]

    with respx.mock:
        respx.get(url__regex=r".*hotels\.availabilities\.prices.*").mock(
            return_value=httpx.Response(200, json=mock_response)
        )

        checkin = (datetime.now() + timedelta(days=10)).isoformat()
        checkout = (datetime.now() + timedelta(days=11)).isoformat()

        response = client.post(
            "/watches",
            json={
                "hotel_code": "00088",
                "checkin_date": checkin,
                "checkout_date": checkout,
                "user_id": "user123",
                "callback_url": "https://example.com/callback",
            },
            headers={"X-API-Key": api_key},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["hotel_code"] == "00088"
        assert data["user_id"] == "user123"
        assert data["last_available"] is False


def test_create_watch_invalid_hotel(client: TestClient, api_key: str):
    response = client.post(
        "/watches",
        json={
            "hotel_code": "99999",  # Invalid
            "checkin_date": datetime.now().isoformat(),
            "checkout_date": (datetime.now() + timedelta(days=1)).isoformat(),
            "user_id": "user123",
            "callback_url": "https://example.com/callback",
        },
        headers={"X-API-Key": api_key},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid hotel code"


def test_list_watches(client: TestClient, session: Session, api_key: str):
    # Add a watch manually
    watch = Watch(
        hotel_code="00088",
        checkin_date=datetime.now(),
        checkout_date=datetime.now() + timedelta(days=1),
        user_id="user456",
        callback_url="https://example.com",
    )
    session.add(watch)
    session.commit()

    response = client.get("/watches/user456", headers={"X-API-Key": api_key})
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["hotel_code"] == "00088"


def _admin_headers() -> dict[str, str]:
    token = base64.b64encode(b"admin:admin").decode("ascii")
    return {"Authorization": f"Basic {token}"}


def test_admin_dashboard_requires_auth(client: TestClient):
    response = client.get("/admin")
    assert response.status_code == 401


def test_admin_api_key_create_and_toggle(client: TestClient, session: Session):
    response = client.post(
        "/admin/api-keys",
        data={"client_name": "Panel Bot"},
        headers=_admin_headers(),
    )
    assert response.status_code == 200
    assert "Panel Bot" in response.text

    key = session.exec(select(APIKey).where(APIKey.client_name == "Panel Bot")).first()
    assert key is not None
    assert key.is_active is True

    response = client.post(
        f"/admin/api-keys/{key.id}/toggle",
        headers=_admin_headers(),
    )
    assert response.status_code == 200

    session.refresh(key)
    assert not key.is_active
