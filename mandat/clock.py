"""Часы шлюза: настоящие и симулированные (для демо, тестов и бенча)."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))


class SystemClock:
    def now(self) -> float:
        return time.time()


class SimClock:
    def __init__(self, start: float):
        self._t = float(start)

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> float:
        self._t += seconds
        return self._t

    def set(self, t: float) -> float:
        self._t = float(t)
        return self._t


def parse_iso(text: str) -> float:
    return datetime.fromisoformat(text).timestamp()


def fmt_time(ts: float, tz: timezone = MSK, ms: bool = False) -> str:
    """ЧЧ:ММ:СС в часовом поясе tz; доли секунды показываются, если они есть (или всегда при ms=True)."""
    dt = datetime.fromtimestamp(ts, tz)
    base = dt.strftime("%H:%M:%S")
    return base + f".{dt.microsecond // 1000:03d}" if (dt.microsecond or ms) else base
