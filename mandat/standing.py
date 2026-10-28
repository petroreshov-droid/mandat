"""Поручения клиента (SPEC 6). Агент исполняет их сам, но заявки идут через тот же шлюз и те же проверки."""

from __future__ import annotations

import math
from typing import Any

EPS = 1e-9  # защита от 2.9999999999999996 вместо 3 при делении денег на цену


def _units(amount_rub: float, price: float, rate: float, lot: int) -> int:
    """Сколько бумаг (кратно лоту) можно купить на сумму."""
    return math.floor(amount_rub / (price * rate) / lot + EPS) * lot


def _lowest_share(market: dict[str, Any], candidates: list[str]) -> str | None:
    """Бумага с наименьшей долей в портфеле (знаменатель общий, поэтому сравниваем стоимость позиций)."""
    best: tuple[float, str] | None = None
    for ticker in candidates:
        inst = market["instruments"].get(ticker)
        if inst is None:
            continue
        value = market["positions"].get(ticker, 0) * inst["last"] * market["fx"][inst["currency"]]
        if best is None or value < best[0]:
            best = (value, ticker)
    return best[1] if best else None


def _reinvest_order(market: dict[str, Any], standing: dict[str, Any], amount: float) -> dict[str, Any] | None:
    ticker = _lowest_share(market, standing["from"])
    asks = market["books"].get(ticker, {}).get("asks") if ticker else None
    if not asks:
        return None
    inst, ask = market["instruments"][ticker], asks[0][0]
    quantity = _units(amount, ask, market["fx"][inst["currency"]], inst["lot"])
    if quantity == 0:
        return None
    return {"ticker": ticker, "side": "buy", "quantity": quantity, "price": ask}


def _dca_order(market: dict[str, Any], standing: dict[str, Any]) -> dict[str, Any] | None:
    inst = market["instruments"].get(standing["ticker"])
    if inst is None:
        return None
    quantity = _units(standing["amount_rub"], inst["last"], market["fx"][inst["currency"]], inst["lot"])
    if quantity == 0:
        return None
    return {"ticker": standing["ticker"], "side": "buy", "quantity": quantity, "price": None}


def _run(gateway: Any, standing: dict[str, Any], plan: dict[str, Any] | None, order_id: str) -> dict[str, Any]:
    result = None
    if plan is not None:
        result = gateway.post_order(
            order_id=order_id,
            agent=f"standing:{standing['id']}",
            reason=f"Поручение клиента «{standing['id']}»",
            **plan,
        )
    return {"standing_id": standing["id"], "order": plan, "result": result}


def on_coupon(gateway: Any, amount: float, *, event_id: str | None = None, credit: bool = True) -> list[dict[str, Any]]:
    """Купон на сумму amount: кэш растёт, затем агент покупает бумагу с наименьшей долей по лучшей цене продажи.

    credit=False, если брокер зачислил купон сам (боевой режим). event_id делает повтор события безопасным:
    второй заказ с тем же номером шлюз отклонит как DUPLICATE.
    """
    if credit:
        credit_cash = getattr(gateway.broker, "credit_cash", None)
        if credit_cash is None:
            raise TypeError("Этот брокер сам зачисляет купоны: вызывайте on_coupon(..., credit=False)")
        credit_cash(amount)
    event_id = event_id or str(int(gateway.clock.now()))
    market = gateway.broker.snapshot()
    done = []
    for so in gateway.mandate["standing_orders"]:
        if so["on"] == "coupon" and so["action"] == "buy_lowest_share":
            plan = _reinvest_order(market, so, amount)
            done.append(_run(gateway, so, plan, f"{so['id']}-{event_id}"))
    return done


def on_monthly(gateway: Any, *, day: int | None = None) -> list[dict[str, Any]]:
    """Наступил день месяца (по умолчанию - сегодняшний по часам шлюза): исполняются поручения на этот день."""
    today = gateway.today()
    day = today.day if day is None else day
    market = gateway.broker.snapshot()
    done = []
    for so in gateway.mandate["standing_orders"]:
        if so["on"] == "monthly" and so["action"] == "buy_amount" and so["day"] == day:
            done.append(_run(gateway, so, _dca_order(market, so), f"{so['id']}-{today.year}-{today.month:02d}"))
    return done
