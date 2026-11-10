from datetime import datetime, timezone


def normalize_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def deadline_facts(deadline, now=None):
    now = now or datetime.now(timezone.utc)
    if isinstance(deadline, str):
        deadline = datetime.fromisoformat(deadline.replace('Z', '+00:00'))
    deadline = normalize_utc(deadline)
    return {
        'now': now.isoformat(), 'timezone': 'UTC',
        'today': now.strftime('%d.%m.%Y'),
        'deadline': deadline.isoformat() if deadline else None,
        'deadline_date': deadline.strftime('%d.%m.%Y') if deadline else None,
        'deadline_passed': deadline < now if deadline else None,
        'seconds_until_deadline': int((deadline - now).total_seconds()) if deadline else None,
    }
