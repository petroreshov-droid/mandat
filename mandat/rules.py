"""Движок проверок (SPEC 2): чистая функция evaluate(order, mandate, market, state).

Ничего не читает и не пишет снаружи: одинаковые входы дают одинаковый результат.
Проверки идут строго по порядку таблицы; провал 1-5 прерывает оценку.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from .explain import class_ru, fmt_num, fmt_pct, fmt_rub, plural
from .models import ALLOW, ASK, DENY

OK = "ок"
WINDOW_RUNAWAY = 60  # секунд, окно для RUNAWAY
DEPTH_BAND = 0.01  # глубина стакана: уровни в пределах 1% от лучшей цены

EXPLAIN_ALLOW = "В рамках мандата: заявка отправлена брокеру."
EXPLAIN_ASK = "Нужно ваше подтверждение: {detail}"
EXPLAIN_DENY = "Отклонено: {detail}"


def _plain(obj: Any) -> Any:
    return obj.to_dict() if hasattr(obj, "to_dict") else obj


def total_value(market: dict[str, Any]) -> float:
    """cash_rub + стоимость всех позиций в рублях (SPEC 1.1)."""
    instruments, fx = market["instruments"], market["fx"]
    held = 0.0
    for ticker, qty in market.get("positions", {}).items():
        inst = instruments.get(ticker)
        if inst is not None:
            held += qty * inst["last"] * fx[inst["currency"]]
    return market["cash_rub"] + held


def build_view(order: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
    """Всё, что движку нужно от рынка по одной заявке (это же пишется в журнал как snapshot)."""
    ticker = order["ticker"]
    inst = market["instruments"].get(ticker)
    qty = market.get("positions", {}).get(ticker, 0)
    rate = market["fx"][inst["currency"]] if inst is not None else None
    return {
        "cash_rub": market["cash_rub"],
        "total_value": total_value(market),
        "position_value": qty * inst["last"] * rate if inst is not None else 0.0,
        "position_qty": qty,
        "fx": rate,
        "instrument": inst,
        "book": market.get("books", {}).get(ticker),
    }


class _Ctx:
    """Производные величины одной оценки (SPEC 1.1)."""

    __slots__ = (
        "order",
        "m",
        "lim",
        "v",
        "s",
        "inst",
        "qty",
        "price",
        "buy",
        "sell",
        "value",
        "lots",
    )

    def __init__(self, order: dict, mandate: dict, view: dict, state: dict):
        self.order, self.m, self.lim, self.v, self.s = (
            order,
            mandate,
            mandate["limits"],
            view,
            state,
        )
        self.inst = view["instrument"]
        self.qty = order["quantity"]
        self.price = order.get("price")
        self.buy = order["side"] == "buy"
        self.sell = order["side"] == "sell"
        if self.inst is None:
            self.value, self.lots = 0.0, 0.0
        else:
            exec_price = self.price if self.price is not None else self.inst["last"]
            self.value = self.qty * exec_price * view["fx"]
            self.lots = self.qty / self.inst["lot"]


def _depth(ctx: _Ctx) -> float | None:
    """Видимая глубина в лотах; None - стакана нет (нет книги или нужная сторона пуста), проверка пропускается."""
    book = ctx.v["book"]
    levels = (book["asks"] if ctx.buy else book["bids"]) if book else None
    if not levels:
        return None
    best = levels[0][0]
    if ctx.buy:
        return sum(lots for price, lots in levels if price <= best * (1 + DEPTH_BAND))
    return sum(lots for price, lots in levels if price >= best * (1 - DEPTH_BAND))


# --- проверки: каждая возвращает (ok, detail) или None, если пропущена ---


def _kill(c: _Ctx):
    if c.s["kill_switch"]:
        return False, "Агент выключен клиентом."
    return True, OK


def _paused(c: _Ctx):
    if c.s["paused"]:
        return (
            False,
            "Агент на паузе после серии нарушений. Снять паузу может только клиент.",
        )
    return True, OK


def _duplicate(c: _Ctx):
    oid = c.order.get("order_id")
    if oid is not None and oid in c.s["seen_order_ids"]:
        return False, f"Повтор заявки {oid}: она уже обработана, вторая не отправлена."
    return True, OK


def _runaway(c: _Ctx):
    limit = c.lim.get("max_orders_per_minute")
    if limit is None:
        return None
    now = c.s["now"]
    n = sum(1 for ts in c.s["recent_order_ts"] if now - ts < WINDOW_RUNAWAY)
    if n >= limit:
        counted = plural(n + 1, "заявка", "заявки", "заявок")  # считая текущую: 6-я заявка при лимите 5
        return False, f"Похоже на зацикливание: {counted} за минуту при лимите {limit}. Агент поставлен на паузу."
    return True, OK


def _instrument(c: _Ctx):
    if c.inst is None:
        return False, f"Бумага {c.order['ticker']} не найдена."
    return True, OK


def _status(c: _Ctx):
    if c.inst["status"] != "normal":
        return False, f"Торги по {c.inst['name']} сейчас не идут."
    return True, OK


def _class(c: _Ctx):
    cls = c.inst["class"]
    if cls not in c.m["allowed_classes"]:
        return False, f"Класс «{class_ru(cls)}» запрещён мандатом."
    return True, OK


def _qual_only(c: _Ctx):
    if c.inst["qual_only"] and c.m["forbid_qual_only"]:
        return False, f"{c.inst['name']} - только для квалифицированных инвесторов."
    return True, OK


def _currency(c: _Ctx):
    cur = c.inst["currency"]
    if cur not in c.m["allowed_currencies"]:
        return False, f"Валюта {cur.upper()} не разрешена мандатом."
    return True, OK


def _lot(c: _Ctx):
    lot = c.inst["lot"]
    if c.qty <= 0 or c.qty % lot != 0:
        return False, f"Количество {fmt_num(c.qty, 4)} не кратно лоту {lot}."
    return True, OK


def _side(c: _Ctx):
    if c.sell:
        if not c.m["allow_sell"]:
            return False, "Продажи по мандату запрещены."
        if c.m["forbid_leverage"] and c.qty > c.v["position_qty"]:
            return False, "Продажа больше позиции - это шорт, запрещено."
    return True, OK


def _price_deviation(c: _Ctx):
    limit = c.lim.get("max_price_deviation")
    if limit is None:
        return None
    if c.price is None:
        return True, OK
    last = c.inst["last"]
    dev = abs(c.price - last) / last
    if dev > limit:
        return False, (
            f"Цена {fmt_num(c.price, 4)} отличается от рынка ({fmt_num(last, 4)}) "
            f"на {fmt_pct(dev)}, лимит {fmt_pct(limit)}."
        )
    return True, OK


def _funds(c: _Ctx):
    cash = c.v["cash_rub"]
    if c.buy and c.m["forbid_leverage"] and c.value > cash:
        return (
            False,
            f"Не хватает денег: нужно {fmt_rub(c.value)}, свободно {fmt_rub(cash)}. Плечо запрещено.",
        )
    return True, OK


def _whitelist(c: _Ctx):
    allowed = c.m["allowed_tickers"]
    if allowed and c.order["ticker"] not in allowed:
        return False, f"Бумаги {c.inst['name']} нет в вашем списке."
    return True, OK


def _position_share(c: _Ctx):
    limit = c.lim.get("max_position_share")
    if limit is None:
        return None
    if not c.buy:
        return True, OK
    total = c.v["total_value"]
    after = c.v["position_value"] + c.value
    # как в JS: x/0 = Infinity, 0/0 = NaN (сравнение с NaN ложно)
    share = after / total if total != 0 else (math.inf if after > 0 else 0.0)
    if share > limit:
        shown = fmt_pct(share) if math.isfinite(share) else "более 100%"
        return (
            False,
            f"После покупки доля {c.inst['name']} станет {shown}, лимит {fmt_pct(limit)}.",
        )
    return True, OK


def _order_value(c: _Ctx):
    limit = c.lim.get("max_order_value")
    if limit is None:
        return None
    if c.value > limit:
        return False, f"Заявка на {fmt_rub(c.value)} больше лимита {fmt_rub(limit)}."
    return True, OK


def _daily_turnover(c: _Ctx):
    limit = c.lim.get("daily_turnover")
    if limit is None:
        return None
    used = c.s["turnover_today"]
    if used + c.value > limit:
        return (
            False,
            f"Дневной лимит {fmt_rub(limit)} будет превышен: уже {fmt_rub(used)}.",
        )
    return True, OK


def _ask_threshold(c: _Ctx):
    limit = c.lim.get("ask_above")
    if limit is None:
        return None
    if c.value > limit:
        return (
            False,
            f"Заявка на {fmt_rub(c.value)}: крупные заявки по мандату подтверждаете вы.",
        )
    return True, OK


def _book_depth(c: _Ctx):
    limit = c.lim.get("max_book_share")
    if limit is None:
        return None
    depth = _depth(c)
    if depth is None:
        return None
    if c.lots > limit * depth:
        return False, f"Заявка забирает {fmt_num(c.lots / depth * 100, 1)}% видимого стакана: возможен сдвиг цены."
    return True, OK


def _whitelist_severity(c: _Ctx) -> str:
    return "hard" if c.m["strict_whitelist"] else "soft"


# (код, уровень или функция уровня, прерывает ли оценку при провале, проверка)
_PIPELINE: tuple[tuple[str, str | Callable[[_Ctx], str], bool, Callable[[_Ctx], Any]], ...] = (
    ("KILL", "hard", True, _kill),
    ("PAUSED", "hard", True, _paused),
    ("DUPLICATE", "hard", True, _duplicate),
    ("RUNAWAY", "hard", True, _runaway),
    ("INSTRUMENT", "hard", True, _instrument),
    ("TRADING_STATUS", "hard", False, _status),
    ("CLASS", "hard", False, _class),
    ("QUAL_ONLY", "hard", False, _qual_only),
    ("CURRENCY", "hard", False, _currency),
    ("LOT", "hard", False, _lot),
    ("SIDE", "hard", False, _side),
    ("PRICE_DEVIATION", "hard", False, _price_deviation),
    ("FUNDS", "hard", False, _funds),
    ("WHITELIST", _whitelist_severity, False, _whitelist),
    ("POSITION_SHARE", "hard", False, _position_share),
    ("ORDER_VALUE", "hard", False, _order_value),
    ("DAILY_TURNOVER", "hard", False, _daily_turnover),
    ("ASK_THRESHOLD", "soft", False, _ask_threshold),
    ("BOOK_DEPTH", "soft", False, _book_depth),
)
CHECK_CODES = tuple(code for code, *_ in _PIPELINE)
STOP_CODES = tuple(code for code, _, stop, _ in _PIPELINE if stop)


def evaluate_view(
    order: dict[str, Any],
    mandate: dict[str, Any],
    view: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    """Оценка по готовому снимку (view). evaluate() = build_view() + evaluate_view()."""
    ctx = _Ctx(order, mandate, view, state)
    checks: list[dict[str, Any]] = []
    actions: list[str] = []
    first_hard = first_soft = None
    for code, severity, stop, check in _PIPELINE:
        result = check(ctx)
        if result is None:
            continue
        ok, detail = result
        level = severity(ctx) if callable(severity) else severity
        checks.append({"code": code, "ok": ok, "severity": level, "detail": detail})
        if ok:
            continue
        if level == "hard":
            first_hard = first_hard or detail
        else:
            first_soft = first_soft or detail
        if code == "RUNAWAY":
            actions.append("PAUSE")
        if stop:
            break
    if first_hard:
        decision, explanation = DENY, EXPLAIN_DENY.format(detail=first_hard)
    elif first_soft:
        decision, explanation = ASK, EXPLAIN_ASK.format(detail=first_soft)
    else:
        decision, explanation = ALLOW, EXPLAIN_ALLOW
    return {
        "decision": decision,
        "checks": checks,
        "explanation": explanation,
        "order_value": round(ctx.value, 2),
        "actions": actions,
    }


def evaluate(order: Any, mandate: Any, market: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    order, mandate = _plain(order), _plain(mandate)
    return evaluate_view(order, mandate, build_view(order, market), state)
