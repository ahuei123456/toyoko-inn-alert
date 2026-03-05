import base64
from datetime import datetime, timedelta

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from toyoko_inn_alert.api import ADMIN_PASSWORD, ADMIN_USERNAME, app, get_session
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
def client_fixture(session: Session, monkeypatch):
    monkeypatch.setenv("WEBHOOK_SIGNATURE_SECRET", "test-signing-secret")

    def get_session_override():
        return session

    app.dependency_overrides[get_session] = get_session_override
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def _assert_error_contract(
    response,
    expected_status: int,
    expected_code: str,
    expected_message: str | None = None,
):
    assert response.status_code == expected_status
    detail = response.json()["detail"]
    assert set(detail.keys()) == {"code", "message"}
    assert detail["code"] == expected_code
    if expected_message is not None:
        assert detail["message"] == expected_message


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
    _assert_error_contract(
        response,
        400,
        "INVALID_HOTEL_CODE",
        "Invalid hotel code",
    )


def test_create_watch_invalid_date_range_contract(client: TestClient, api_key: str):
    now = datetime.now()
    response = client.post(
        "/watches",
        json={
            "hotel_code": "00088",
            "checkin_date": (now + timedelta(days=2)).isoformat(),
            "checkout_date": (now + timedelta(days=1)).isoformat(),
            "user_id": "user123",
            "callback_url": "https://example.com/callback",
        },
        headers={"X-API-Key": api_key},
    )
    _assert_error_contract(
        response,
        400,
        "INVALID_DATE_RANGE",
        "Check-in must be before check-out",
    )


def test_missing_api_key_returns_machine_readable_error(client: TestClient):
    response = client.get("/watches/user123")
    _assert_error_contract(
        response,
        401,
        "INVALID_API_KEY",
        "Missing or invalid API key",
    )


def test_invalid_api_key_returns_machine_readable_error(client: TestClient):
    response = client.get("/watches/user123", headers={"X-API-Key": "bad_key"})
    _assert_error_contract(
        response,
        401,
        "INVALID_API_KEY",
        "Missing or invalid API key",
    )


def test_create_watch_max_active_watches(
    client: TestClient, session: Session, api_key: str
):
    # Add 10 watches manually
    for i in range(10):
        watch = Watch(
            hotel_code="00088",
            checkin_date=datetime.now() + timedelta(days=i),
            checkout_date=datetime.now() + timedelta(days=i + 1),
            user_id="user_max",
            callback_url="https://example.com",
        )
        session.add(watch)
    session.commit()

    response = client.post(
        "/watches",
        json={
            "hotel_code": "00088",
            "checkin_date": (datetime.now() + timedelta(days=20)).isoformat(),
            "checkout_date": (datetime.now() + timedelta(days=21)).isoformat(),
            "user_id": "user_max",
            "callback_url": "https://example.com/callback",
        },
        headers={"X-API-Key": api_key},
    )
    _assert_error_contract(
        response,
        409,
        "MAX_ACTIVE_WATCHES",
        "You can only have up to 10 active watches.",
    )


def test_create_watch_duplicate(client: TestClient, session: Session, api_key: str):
    checkin = datetime.now() + timedelta(days=10)
    checkout = datetime.now() + timedelta(days=11)

    # Add 1 watch manually
    watch = Watch(
        hotel_code="00088",
        checkin_date=checkin,
        checkout_date=checkout,
        user_id="user_dup",
        callback_url="https://example.com",
    )
    session.add(watch)
    session.commit()

    response = client.post(
        "/watches",
        json={
            "hotel_code": "00088",
            "checkin_date": checkin.isoformat(),
            "checkout_date": checkout.isoformat(),
            "user_id": "user_dup",
            "callback_url": "https://example.com/callback",
        },
        headers={"X-API-Key": api_key},
    )
    _assert_error_contract(
        response,
        409,
        "DUPLICATE_WATCH",
        "Watch already exists for this user and date",
    )


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


def test_delete_watch_not_found_returns_machine_readable_error(
    client: TestClient, api_key: str
):
    response = client.delete("/watches/999999", headers={"X-API-Key": api_key})
    _assert_error_contract(
        response,
        404,
        "WATCH_NOT_FOUND",
        "Watch not found",
    )


def _admin_headers() -> dict[str, str]:
    token = base64.b64encode(
        f"{ADMIN_USERNAME}:{ADMIN_PASSWORD}".encode("ascii")
    ).decode("ascii")
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


def test_request_id_is_echoed_when_provided(client: TestClient):
    request_id = "req-12345"
    response = client.get("/status", headers={"X-Request-ID": request_id})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == request_id


def test_request_id_is_generated_when_missing(client: TestClient):
    response = client.get("/status")
    assert response.status_code == 200
    generated = response.headers.get("X-Request-ID")
    assert generated is not None
    assert len(generated) > 0
