"""Стресс-тест мандата (SPEC 5, детерминированная часть).

Мандат с замечанием уровня critical подписать нельзя, пока оно не исправлено.
"""

from __future__ import annotations

import copy
from typing import Any

WARN, CRITICAL = "warn", "critical"


def _set_path(data: dict[str, Any], path: str, value: Any) -> None:
    *parents, leaf = path.split(".")
    for key in parents:
        data = data[key]
    data[leaf] = value


def _finding(code: str, level: str, text: str, fix_text: str | None = None, patch: dict | None = None) -> dict:
    fix = {"text": fix_text, "patch": patch} if patch else None
    return {"code": code, "level": level, "text": text, "fix": fix}


def lint(mandate: dict[str, Any]) -> list[dict[str, Any]]:
    """Список замечаний в порядке таблицы SPEC 5. Пустой список - дыр не найдено."""
    lim = mandate["limits"]
    found: list[dict[str, Any]] = []

    if lim.get("daily_turnover") is None:
        found.append(
            _finding(
                "L_NO_DAILY",
                WARN,
                "Нет дневного лимита: агент сможет потратить весь кэш за один день.",
                "daily_turnover = 100000",
                {"limits.daily_turnover": 100000},
            )
        )
    if lim.get("max_order_value") is None:
        found.append(
            _finding(
                "L_NO_ORDER_MAX",
                WARN,
                "Нет лимита на одну заявку.",
                "max_order_value = 50000",
                {"limits.max_order_value": 50000},
            )
        )
    if lim.get("max_orders_per_minute") is None:
        found.append(
            _finding(
                "L_NO_RUNAWAY",
                CRITICAL,
                "Нет защиты от зацикливания: робот сможет отправить сотни заявок.",
                "max_orders_per_minute = 5",
                {"limits.max_orders_per_minute": 5},
            )
        )
    if not mandate["allowed_tickers"]:
        found.append(
            _finding(
                "L_NO_WHITELIST",
                WARN,
                "Нет списка бумаг: агент сможет купить любую бумагу разрешённых классов.",
            )
        )
    if mandate["allow_sell"]:
        found.append(
            _finding("L_SELL_OPEN", WARN, "Продажи разрешены без условий.", "allow_sell = false", {"allow_sell": False})
        )
    if not mandate["forbid_leverage"]:
        found.append(
            _finding(
                "L_LEVERAGE",
                CRITICAL,
                "Плечо разрешено: убыток может превысить вложенное.",
                "forbid_leverage = true",
                {"forbid_leverage": True},
            )
        )
    if "futures" in mandate["allowed_classes"]:
        classes = [c for c in mandate["allowed_classes"] if c != "futures"]
        found.append(
            _finding(
                "L_DERIVATIVES",
                CRITICAL,
                "Разрешены фьючерсы: это сделки с плечом.",
                "убрать futures",
                {"allowed_classes": classes},
            )
        )
    if lim.get("max_position_share") is None:
        found.append(
            _finding(
                "L_NO_SHARE",
                WARN,
                "Нет лимита доли: портфель может оказаться в одной бумаге.",
                "max_position_share = 0.15",
                {"limits.max_position_share": 0.15},
            )
        )
    elif lim["max_position_share"] > 0.5:
        found.append(
            _finding(
                "L_SHARE_HIGH",
                WARN,
                "Лимит доли выше 50%.",
                "max_position_share = 0.15",
                {"limits.max_position_share": 0.15},
            )
        )
    caps = [lim.get("max_order_value"), lim.get("daily_turnover")]
    for so in mandate["standing_orders"]:
        amount = so.get("amount_rub")
        if amount is not None and any(cap is not None and amount > cap for cap in caps):
            found.append(
                _finding("L_STANDING_CONFLICT", WARN, f"Поручение «{so['id']}» больше лимита: оно не исполнится.")
            )
    return found


def has_critical(findings: list[dict[str, Any]]) -> bool:
    return any(f["level"] == CRITICAL for f in findings)


def apply_fixes(
    mandate: dict[str, Any], findings: list[dict[str, Any]], codes: set[str] | None = None
) -> dict[str, Any]:
    """Копия мандата с применёнными исправлениями (все или только для codes). Подпись остаётся за клиентом."""
    fixed = copy.deepcopy(mandate)
    for f in findings:
        if f["fix"] and (codes is None or f["code"] in codes):
            for path, value in f["fix"]["patch"].items():
                _set_path(fixed, path, value)
    return fixed
