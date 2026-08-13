from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from config import DATABASE_URL
from db.models import Base

_is_sqlite = DATABASE_URL.startswith("sqlite")
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 15} if _is_sqlite else {},
)

if _is_sqlite:
    # WAL lets readers and a writer run concurrently instead of locking the whole
    # file; busy_timeout makes a writer wait instead of immediately raising
    # "database is locked" when a bot command collides with another in-flight one.
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return SessionLocal()
