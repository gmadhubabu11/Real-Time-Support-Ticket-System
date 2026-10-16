from datetime import datetime, timezone


def utcnow() -> datetime:
    """Naive UTC datetime (all DB datetimes are stored as naive UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
