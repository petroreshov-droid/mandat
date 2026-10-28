"""Типизированные модели. Движок работает на обычных dict (JSON), модели стоят на границах."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from typing import Any

from .canonical import sha256_hex

ALLOW, ASK, DENY = "ALLOW", "ASK", "DENY"
SIDES = ("buy", "sell")
LIMIT_KEYS = (
    "max_position_share",
    "max_order_value",
    "daily_turnover",
    "ask_above",
    "max_price_deviation",
    "max_book_share",
    "max_orders_per_minute",
)


class OrderFormatError(ValueError):
    """Заявка не соответствует схеме (SPEC 1.3): до движка и брокера не доходит."""


def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


@dataclass(frozen=True)
class Order:
    ticker: str
    side: str
    quantity: int | float
    price: float | None = None
    order_id: str | None = None
    agent: str = "agent"
    reason: str = ""

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> Order:
        ticker, side, qty, price = raw.get("ticker"), raw.get("side"), raw.get("quantity"), raw.get("price")
        order_id = raw.get("order_id")
        if not isinstance(ticker, str) or not ticker.strip():
            raise OrderFormatError("Не указан тикер бумаги.")
        if side not in SIDES:
            raise OrderFormatError("Сторона заявки должна быть «buy» или «sell».")
        if not _is_number(qty):
            raise OrderFormatError("Количество должно быть числом (штук, не лотов).")
        if isinstance(qty, float) and qty.is_integer():
            qty = int(qty)
        if price is not None and (not _is_number(price) or price <= 0):
            raise OrderFormatError("Цена должна быть положительным числом или null (рыночная заявка).")
        if order_id is not None and (not isinstance(order_id, str) or not order_id):
            raise OrderFormatError("order_id должен быть непустой строкой или null.")
        agent, reason = raw.get("agent", "agent"), raw.get("reason", "")
        if not isinstance(agent, str) or not isinstance(reason, str):
            raise OrderFormatError("Поля agent и reason должны быть строками.")
        return cls(ticker.strip(), side, qty, price, order_id, agent[:100], reason[:1000])

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "ticker": self.ticker,
            "side": self.side,
            "quantity": self.quantity,
            "price": self.price,
            "agent": self.agent,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Limits:
    max_position_share: float | None = None
    max_order_value: float | None = None
    daily_turnover: float | None = None
    ask_above: float | None = None
    max_price_deviation: float | None = None
    max_book_share: float | None = None
    max_orders_per_minute: int | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Limits:
        return cls(**{k: raw.get(k) for k in LIMIT_KEYS})

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in LIMIT_KEYS}


@dataclass(frozen=True)
class Mandate:
    title: str
    client_text: str
    allowed_classes: tuple[str, ...]
    allowed_tickers: tuple[str, ...]
    strict_whitelist: bool
    allowed_currencies: tuple[str, ...]
    allow_sell: bool
    forbid_leverage: bool
    forbid_qual_only: bool
    limits: Limits
    standing_orders: tuple[dict[str, Any], ...] = ()
    schema: str = "mandat/v1"

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Mandate:
        try:
            return cls(
                title=raw["title"],
                client_text=raw["client_text"],
                allowed_classes=tuple(raw["allowed_classes"]),
                allowed_tickers=tuple(raw["allowed_tickers"]),
                strict_whitelist=raw["strict_whitelist"],
                allowed_currencies=tuple(raw["allowed_currencies"]),
                allow_sell=raw["allow_sell"],
                forbid_leverage=raw["forbid_leverage"],
                forbid_qual_only=raw["forbid_qual_only"],
                limits=Limits.from_dict(raw["limits"]),
                standing_orders=tuple(dict(so) for so in raw["standing_orders"]),
                schema=raw["schema"],
            )
        except KeyError as exc:
            raise ValueError(f"В мандате нет поля {exc.args[0]}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "title": self.title,
            "client_text": self.client_text,
            "allowed_classes": list(self.allowed_classes),
            "allowed_tickers": list(self.allowed_tickers),
            "strict_whitelist": self.strict_whitelist,
            "allowed_currencies": list(self.allowed_currencies),
            "allow_sell": self.allow_sell,
            "forbid_leverage": self.forbid_leverage,
            "forbid_qual_only": self.forbid_qual_only,
            "limits": self.limits.to_dict(),
            "standing_orders": [dict(so) for so in self.standing_orders],
        }

    @property
    def hash(self) -> str:
        return sha256_hex(self.to_dict())


@dataclass
class GatewayState:
    """Состояние шлюза (SPEC 1.4). as_dict() отдаёт ссылки на списки без копирования."""

    now: float = 0.0
    turnover_today: float = 0.0
    recent_order_ts: list[float] = field(default_factory=list)
    seen_order_ids: list[str] = field(default_factory=list)
    violation_ts: list[float] = field(default_factory=list)
    paused: bool = False
    kill_switch: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def fresh_state(**overrides: Any) -> dict[str, Any]:
    """Полное состояние для движка со значениями по умолчанию (удобно в тестах и бенче)."""
    return GatewayState(**overrides).as_dict()


@dataclass
class Pending:
    """Заявка, которая ждёт подтверждения клиента."""

    pending_id: str
    order: dict[str, Any]
    ts: float
    mandate_hash: str
    seq: int
    status: str = "pending"  # pending | confirmed | rejected | failed
