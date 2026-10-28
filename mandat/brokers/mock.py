"""Брокер в памяти: рынок из фикстуры, исполнение по exec_price без комиссии."""

from __future__ import annotations

import copy
from typing import Any

from ..fixtures import load_market
from .base import Broker


class MockBroker(Broker):
    """exchange_rules=True: как биржа, отклоняет неизвестную бумагу, не кратный лоту объём и закрытые торги.

    Мандат брокер не знает: плечо, лимиты и списки бумаг ему безразличны.
    """

    def __init__(self, market: dict[str, Any] | None = None, *, exchange_rules: bool = True):
        self._market = copy.deepcopy(market if market is not None else load_market())
        self._exchange_rules = exchange_rules
        self._counter = 0
        self.orders: list[dict[str, Any]] = []  # все ответы брокера, в порядке поступления

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self._market)

    def credit_cash(self, amount: float) -> None:
        """Зачисление (например, купона)."""
        self._market["cash_rub"] += amount

    def place_order(self, order: dict[str, Any]) -> dict[str, Any]:
        self._counter += 1
        response = self._execute(order, f"MOCK-{self._counter:06d}")
        self.orders.append({"order": copy.deepcopy(order), **response})
        return response

    def _execute(self, order: dict[str, Any], order_id: str) -> dict[str, Any]:
        def rejected(reason: str) -> dict[str, Any]:
            return {"order_id": order_id, "status": "rejected", "exec_price": None, "reason": reason}

        ticker, qty = order["ticker"], order["quantity"]
        inst = self._market["instruments"].get(ticker)
        if inst is None:
            return rejected("Бумага не найдена")
        if self._exchange_rules:
            if inst["status"] != "normal":
                return rejected("Торги по бумаге не идут")
            if qty <= 0 or qty % inst["lot"] != 0:
                return rejected("Количество не кратно лоту")
        price = order.get("price")
        exec_price = price if price is not None else inst["last"]
        value = qty * exec_price * self._market["fx"][inst["currency"]]
        sign = 1 if order["side"] == "buy" else -1
        self._market["cash_rub"] -= sign * value
        positions = self._market["positions"]
        held = positions.get(ticker, 0) + sign * qty
        if held:
            positions[ticker] = held
        else:
            positions.pop(ticker, None)
        return {"order_id": order_id, "status": "filled", "exec_price": exec_price, "reason": None}
