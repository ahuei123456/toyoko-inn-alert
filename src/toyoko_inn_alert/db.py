from datetime import UTC, datetime

from sqlalchemy import event
from sqlmodel import Field, Index, Session, SQLModel, create_engine

WATCH_UNIQUE_INDEX_NAME = "ix_watch_unique_user_hotel_stay"


def get_now():
    return datetime.now(UTC)


class Watch(SQLModel, table=True):
    __table_args__ = (
        Index(
            WATCH_UNIQUE_INDEX_NAME,
            "user_id",
            "hotel_code",
            "checkin_date",
            "checkout_date",
            unique=True,
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    hotel_code: str = Field(index=True)
    checkin_date: datetime
    checkout_date: datetime
    num_people: int = 1
    smoking_type: str = "noSmoking"
    room_type: int = 10  # Placeholder
    user_id: str = Field(index=True)
    callback_url: str
    last_available: bool = False
    created_at: datetime = Field(default_factory=get_now)


class Notification(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    watch_id: int = Field(foreign_key="watch.id")
    status: str = "pending"  # pending, sent, failed
    retry_count: int = 0
    last_retry: datetime | None = None
    created_at: datetime = Field(default_factory=get_now)
    payload: str  # JSON string


class APIKey(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    key: str = Field(index=True, unique=True)
    client_name: str
    is_active: bool = True
    created_at: datetime = Field(default_factory=get_now)


sqlite_file_name = "toyoko.db"
sqlite_url = f"sqlite:///{sqlite_file_name}"

connect_args = {"check_same_thread": False}
engine = create_engine(sqlite_url, connect_args=connect_args)


@event.listens_for(engine, "connect")
def configure_sqlite_connection(dbapi_connection, connection_record):
    del connection_record
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            f"{WATCH_UNIQUE_INDEX_NAME} "
            "ON watch (user_id, hotel_code, checkin_date, checkout_date)"
        )


def get_session():
    with Session(engine) as session:
        yield session
