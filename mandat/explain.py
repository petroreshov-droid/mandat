"""Тексты для людей: числа по-русски и правила мандата простыми словами."""

from __future__ import annotations

import math
from typing import Any

CLASS_RU = {"share": "акции", "bond": "облигации", "etf": "фонды", "futures": "фьючерсы"}
CURRENCY_RU = {"rub": "рубли", "cny": "юани", "usd": "доллары", "eur": "евро"}


def fmt_num(x: float, digits: int = 2) -> str:
    """1234567.5 -> «1 234 567,5»: пробел между тысячами, запятая, без хвостовых нулей."""
    if not math.isfinite(x):
        return "∞" if x > 0 else "-∞"
    text = f"{x:,.{digits}f}".replace(",", " ")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    text = text.replace(".", ",")
    return "0" if text == "-0" else text


def fmt_rub(x: float) -> str:
    """4200 -> «4 200 ₽», 3622.4 -> «3 622,40 ₽»."""
    if abs(x - round(x)) < 0.005:
        return f"{fmt_num(round(x), 0)} ₽"
    return f"{f'{x:,.2f}'.replace(',', ' ').replace('.', ',')} ₽"


def fmt_pct(fraction: float, digits: int = 2) -> str:
    """0.0055 -> «0,55%»."""
    return f"{fmt_num(fraction * 100, digits)}%"


def plural(n: int, one: str, few: str, many: str) -> str:
    """5 записей, 1 запись, 3 записи: plural(n, "запись", "записи", "записей")."""
    if 11 <= n % 100 <= 14:
        form = many
    else:
        form = one if n % 10 == 1 else few if 2 <= n % 10 <= 4 else many
    return f"{n} {form}"


def class_ru(cls: str) -> str:
    return CLASS_RU.get(cls, cls)


def mandate_rules(mandate: dict, names: dict[str, str] | None = None) -> list[str]:
    """Правила мандата простыми словами (для инструмента get_mandate и демо)."""
    names = names or {}
    lim: dict[str, Any] = mandate["limits"]
    rules = ["Можно торговать: " + ", ".join(class_ru(c) for c in mandate["allowed_classes"]) + "."]
    rules.append("Валюты: " + ", ".join(CURRENCY_RU.get(c, c.upper()) for c in mandate["allowed_currencies"]) + ".")

    tickers = mandate["allowed_tickers"]
    if tickers:
        listed = ", ".join(names.get(t, t) for t in tickers)
        outside = "запрещена." if mandate["strict_whitelist"] else "только с вашего подтверждения."
        rules.append(f"Только из вашего списка: {listed}. Бумага вне списка - {outside}")
    else:
        rules.append("Списка бумаг нет: можно любую бумагу разрешённых классов.")

    rules.append("Продажи разрешены." if mandate["allow_sell"] else "Продажи запрещены.")
    if mandate["forbid_leverage"]:
        rules.append("Плечо запрещено: покупка только на свободные деньги, продажа только из имеющихся бумаг.")
    else:
        rules.append("Плечо разрешено.")
    if mandate["forbid_qual_only"]:
        rules.append("Бумаги только для квалифицированных инвесторов запрещены.")

    if lim.get("max_position_share") is not None:
        rules.append(f"Не больше {fmt_pct(lim['max_position_share'])} портфеля в одной бумаге.")
    if lim.get("max_order_value") is not None:
        rules.append(f"Одна заявка - не больше {fmt_rub(lim['max_order_value'])}.")
    if lim.get("daily_turnover") is not None:
        rules.append(f"Оборот за день - не больше {fmt_rub(lim['daily_turnover'])}.")
    if lim.get("ask_above") is not None:
        rules.append(f"Заявки больше {fmt_rub(lim['ask_above'])} подтверждаете вы.")
    if lim.get("max_price_deviation") is not None:
        rules.append(f"Цена в заявке - не дальше {fmt_pct(lim['max_price_deviation'])} от рыночной.")
    if lim.get("max_book_share") is not None:
        rules.append(
            f"Заявка не должна забирать больше {fmt_pct(lim['max_book_share'])} видимого стакана, иначе спросим вас."
        )
    if lim.get("max_orders_per_minute") is not None:
        rules.append(f"Не больше {lim['max_orders_per_minute']} заявок в минуту, иначе агент встаёт на паузу.")

    for so in mandate["standing_orders"]:
        if so["on"] == "coupon":
            pool = ", ".join(names.get(t, t) for t in so["from"])
            rules.append(f"Поручение «{so['id']}»: купон вкладывается в ту бумагу из ({pool}), где ваша доля меньше.")
        elif so["on"] == "monthly":
            what = names.get(so["ticker"], so["ticker"])
            rules.append(
                f"Поручение «{so['id']}»: каждое {so['day']}-е число докупается {what} на {fmt_rub(so['amount_rub'])}."
            )
    rules.append(
        "Менять мандат может только клиент подписью новой версии. Подтверждать заявки и снимать паузу - тоже только клиент."
    )
    return rules
