"""Local-time helpers. Everything is stored as unix seconds plus local day/hour
columns, so reports group by the pharmacy's own calendar regardless of the
Pi's system timezone."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

_tz = ZoneInfo("Europe/Istanbul")


def set_timezone(name: str) -> None:
    global _tz
    _tz = ZoneInfo(name)


def tz() -> ZoneInfo:
    return _tz


def local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, _tz)


def day_str(ts: float) -> str:
    return local(ts).strftime("%Y-%m-%d")


def hour_key(ts: float) -> str:
    """'YYYY-MM-DD HH' in local time."""
    return local(ts).strftime("%Y-%m-%d %H")


def minute_start(ts: float) -> float:
    return ts - (ts % 60)


def hour_start(ts: float) -> float:
    d = local(ts).replace(minute=0, second=0, microsecond=0)
    return d.timestamp()


def day_bounds(day: str) -> tuple[float, float]:
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=_tz)
    return d.timestamp(), (d + timedelta(days=1)).timestamp()


def today() -> str:
    return datetime.now(_tz).strftime("%Y-%m-%d")


def day_range(start: str, end: str) -> list[str]:
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    out = []
    while s <= e:
        out.append(s.isoformat())
        s += timedelta(days=1)
    return out


def shift_day(day: str, n: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


WEEKDAYS_TR = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
