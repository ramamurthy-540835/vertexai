"""Cloud SQL connection management — lazy singleton engine with IAM auth."""

import sqlalchemy
from google.cloud.sql.connector import Connector, IPTypes

import config

_connector: Connector | None = None
_engine: sqlalchemy.Engine | None = None


def get_engine() -> sqlalchemy.Engine:
    """Return a shared SQLAlchemy engine. Lazy-initialized on first call."""
    global _connector, _engine
    if _engine is not None:
        return _engine

    _connector = Connector()

    def getconn():
        return _connector.connect(
            config.DB_INSTANCE,
            "pg8000",
            db=config.DB_NAME,
            user=config.DB_USER,
            enable_iam_auth=True,
            ip_type=IPTypes.PRIVATE,
        )

    _engine = sqlalchemy.create_engine(
        "postgresql+pg8000://",
        creator=getconn,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )
    return _engine