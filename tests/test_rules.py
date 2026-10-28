"""SPEC 2: у каждой из 19 проверок есть тест на провал и на успех."""

import copy

import pytest
from conftest import NOW, code_result, failed, make_order

from mandat.models import Mandate, Order, fresh_state
from mandat.rules import CHECK_CODES, STOP_CODES, build_view, evaluate, evaluate_view


def ok(result, code):
    check = code_result(result, code)
    assert check is not None, f"проверки {code} нет в списке"
    return check["ok"]


def tiny_market(**over):
    """Рынок с круглыми числами: итого 1000 ₽, позиция X на 100 ₽ (точная арифметика для граничных тестов)."""
    inst = {"name": "Икс", "class": "share", "lot": 1, "currency": "rub", "qual_only": False, "status": "normal"}
    market = {
        "as_of": "2026-11-10T10:00:00+03:00",
        "fx": {"rub": 1},
        "cash_rub": 900.0,
        "instruments": {"X": {**inst, "last": 10.0}},
        "positions": {"X": 10},
        "books": {},
    }
    market.update(over)
    return market


def tiny_mandate(**limits):
    return {
        "schema": "mandat/v1",
        "title": "т",
        "client_text": "т",
        "allowed_classes": ["share"],
        "allowed_tickers": [],
        "strict_whitelist": False,
        "allowed_currencies": ["rub"],
        "allow_sell": True,
        "forbid_leverage": True,
        "forbid_qual_only": True,
        "limits": {
            k: limits.get(k)
            for k in (
                "max_position_share",
                "max_order_value",
                "daily_turnover",
                "ask_above",
                "max_price_deviation",
                "max_book_share",
                "max_orders_per_minute",
            )
        },
        "standing_orders": [],
    }


def run_tiny(order, **limits):
    state = limits.pop("state", {})
    return evaluate(order, tiny_mandate(**limits), tiny_market(), fresh_state(now=NOW, **state))


def buy_x(qty, **kw):
    return make_order(ticker="X", quantity=qty, **kw)


# --- 1 KILL ---


def test_kill_fail_stops_evaluation(ev):
    r = ev(state={"kill_switch": True})
    assert r["decision"] == "DENY"
    assert r["checks"] == [{"code": "KILL", "ok": False, "severity": "hard", "detail": "Агент выключен клиентом."}]
    assert r["explanation"] == "Отклонено: Агент выключен клиентом."


def test_kill_pass(ev):
    assert ok(ev(), "KILL")


# --- 2 PAUSED ---


def test_paused_fail_stops_evaluation(ev):
    r = ev(state={"paused": True})
    assert [c["code"] for c in r["checks"]] == ["KILL", "PAUSED"]
    assert r["checks"][1]["detail"] == "Агент на паузе после серии нарушений. Снять паузу может только клиент."
    assert r["decision"] == "DENY" and r["actions"] == []


def test_paused_pass(ev):
    assert ok(ev(), "PAUSED")


# --- 3 DUPLICATE ---


def test_duplicate_fail(ev):
    r = ev(order_id="A1", state={"seen_order_ids": ["A1"]})
    assert [c["code"] for c in r["checks"]] == ["KILL", "PAUSED", "DUPLICATE"]
    assert r["checks"][2]["detail"] == "Повтор заявки A1: она уже обработана, вторая не отправлена."
    assert r["decision"] == "DENY"


def test_duplicate_pass_for_new_or_missing_id(ev):
    assert ok(ev(order_id="A2", state={"seen_order_ids": ["A1"]}), "DUPLICATE")
    assert ok(ev(order_id=None, state={"seen_order_ids": ["A1"]}), "DUPLICATE")


# --- 4 RUNAWAY ---


def test_runaway_fail_pauses_and_stops(ev):
    r = ev(state={"recent_order_ts": [NOW - 1] * 5})
    assert [c["code"] for c in r["checks"]][-1] == "RUNAWAY"
    assert not ok(r, "RUNAWAY")
    assert code_result(r, "RUNAWAY")["detail"] == (
        "Похоже на зацикливание: 6 заявок за минуту при лимите 5. Агент поставлен на паузу."
    )
    assert r["actions"] == ["PAUSE"] and r["decision"] == "DENY"
    assert code_result(r, "INSTRUMENT") is None  # оценка прервана


def test_runaway_detail_counts_the_current_order_and_declines_correctly(ev):
    """Пять прошлых заявок при лимите 5 - это шестая, поэтому «6 заявок»."""

    def detail(prior, limit=5):
        r = ev(limits={"max_orders_per_minute": limit}, state={"recent_order_ts": [NOW - 1] * prior})
        return code_result(r, "RUNAWAY")["detail"]

    assert detail(20).startswith("Похоже на зацикливание: 21 заявка за минуту при лимите 5.")
    assert detail(21).startswith("Похоже на зацикливание: 22 заявки за минуту при лимите 5.")
    assert detail(1, limit=1).startswith("Похоже на зацикливание: 2 заявки за минуту при лимите 1.")


def test_runaway_pass_below_limit(ev):
    r = ev(state={"recent_order_ts": [NOW - 1] * 4})
    assert ok(r, "RUNAWAY") and r["actions"] == []


def test_runaway_window_is_strict_60_seconds(ev):
    assert ok(ev(state={"recent_order_ts": [NOW - 60] * 5}), "RUNAWAY")  # ровно 60 с - уже не в окне
    assert not ok(ev(state={"recent_order_ts": [NOW - 59.5] * 5}), "RUNAWAY")


def test_runaway_skipped_without_limit(ev):
    r = ev(limits={"max_orders_per_minute": None}, state={"recent_order_ts": [NOW - 1] * 50})
    assert code_result(r, "RUNAWAY") is None and r["decision"] == "ALLOW"


# --- 5 INSTRUMENT ---


def test_unknown_instrument_stops_evaluation(ev):
    r = ev(ticker="NOPE")
    assert [c["code"] for c in r["checks"]] == ["KILL", "PAUSED", "DUPLICATE", "RUNAWAY", "INSTRUMENT"]
    assert r["checks"][-1]["detail"] == "Бумага NOPE не найдена."
    assert r["decision"] == "DENY" and r["order_value"] == 0


def test_known_instrument_passes(ev):
    assert ok(ev(), "INSTRUMENT")


# --- 6 TRADING_STATUS ---


@pytest.mark.parametrize("status", ["halted", "closed"])
def test_trading_status_fail(ev, market, status):
    inst = {**market["instruments"]["TMOS"], "status": status}
    r = ev(market_patch={"instruments": {"TMOS": inst}})
    assert not ok(r, "TRADING_STATUS")
    assert code_result(r, "TRADING_STATUS")["detail"] == "Торги по Т-Капитал Индекс МосБиржи сейчас не идут."
    assert r["decision"] == "DENY"


def test_trading_status_pass(ev):
    assert ok(ev(), "TRADING_STATUS")


# --- 7 CLASS ---


@pytest.mark.parametrize(
    ("ticker", "class_ru"),
    [("TMOS", "фонды"), ("SBER", "акции"), ("SiZ6", "фьючерсы"), ("SU26238RMFS4", "облигации")],
)
def test_class_fail_names_class_in_russian(ev, t1, ticker, class_ru):
    only_other = {**t1, "allowed_classes": ["bond" if ticker != "SU26238RMFS4" else "etf"]}
    r = ev(mandate=only_other, ticker=ticker, quantity=1)
    assert code_result(r, "CLASS")["detail"] == f"Класс «{class_ru}» запрещён мандатом."
    assert r["decision"] == "DENY"


def test_class_pass(ev):
    assert ok(ev(), "CLASS")


# --- 8 QUAL_ONLY ---


def test_qual_only_fail(ev):
    r = ev(ticker="STRUCT1")
    assert (
        code_result(r, "QUAL_ONLY")["detail"]
        == "Структурная облигация (демо) - только для квалифицированных инвесторов."
    )
    assert r["decision"] == "DENY"


def test_qual_only_pass_when_not_forbidden_or_not_qual(ev, t1):
    assert ok(ev(mandate={**t1, "forbid_qual_only": False}, ticker="STRUCT1"), "QUAL_ONLY")
    assert ok(ev(), "QUAL_ONLY")


# --- 9 CURRENCY ---


def test_currency_fail(ev):
    r = ev(ticker="SU52002RMFS1", price=984.5)
    assert code_result(r, "CURRENCY")["detail"] == "Валюта CNY не разрешена мандатом."
    assert r["decision"] == "DENY"


def test_currency_pass_and_value_in_rubles(ev, t1):
    r = ev(mandate={**t1, "allowed_currencies": ["rub", "cny"]}, ticker="SU52002RMFS1", price=984.5)
    assert ok(r, "CURRENCY")
    assert r["order_value"] == round(984.5 * 11.2, 2)


# --- 10 LOT ---


@pytest.mark.parametrize("qty", [15, 5, 0, -10])
def test_lot_fail(ev, qty):
    r = ev(ticker="SBER", quantity=qty)
    assert not ok(r, "LOT") and r["decision"] == "DENY"


def test_lot_detail_texts(ev):
    assert code_result(ev(ticker="SBER", quantity=15), "LOT")["detail"] == "Количество 15 не кратно лоту 10."
    assert code_result(ev(ticker="SBER", quantity=-10), "LOT")["detail"] == "Количество -10 не кратно лоту 10."
    assert not ok(ev(ticker="TMOS", quantity=2.5), "LOT")  # дробное количество при лоте 1


def test_lot_pass(ev):
    assert ok(ev(ticker="SBER", quantity=20), "LOT")


# --- 11 SIDE ---


def test_side_fail_when_sells_forbidden(ev):
    r = ev(side="sell", ticker="SBER", quantity=10)
    assert code_result(r, "SIDE")["detail"] == "Продажи по мандату запрещены."
    assert r["decision"] == "DENY"


def test_side_fail_sell_more_than_position_is_short(ev, t1):
    m = {**t1, "allow_sell": True}
    r = ev(mandate=m, side="sell", ticker="SBER", quantity=410)
    assert code_result(r, "SIDE")["detail"] == "Продажа больше позиции - это шорт, запрещено."


def test_side_pass(ev, t1):
    m = {**t1, "allow_sell": True}
    assert ok(ev(mandate=m, side="sell", ticker="SBER", quantity=400), "SIDE")  # ровно вся позиция
    assert ok(ev(), "SIDE")  # покупка
    free = {**m, "forbid_leverage": False}
    assert ok(ev(mandate=free, side="sell", ticker="SBER", quantity=410), "SIDE")  # плечо разрешено - шорт можно


# --- 12 PRICE_DEVIATION ---


def test_price_deviation_fail(ev):
    r = ev(ticker="SBER", quantity=10, price=310.0)
    assert code_result(r, "PRICE_DEVIATION")["detail"] == "Цена 310 отличается от рынка (305,4) на 1,51%, лимит 1%."
    assert r["decision"] == "DENY"


def test_price_deviation_pass(ev):
    assert ok(ev(ticker="SBER", quantity=10, price=305.5), "PRICE_DEVIATION")
    assert ok(ev(ticker="SBER", quantity=10, price=None), "PRICE_DEVIATION")  # рыночная заявка


def test_price_deviation_boundary_is_not_a_failure():
    market = tiny_market()
    market["instruments"]["X"]["last"] = 100.0
    market["positions"] = {}
    market["cash_rub"] = 10000.0
    state = fresh_state(now=NOW)
    r = evaluate(buy_x(1, price=101.0), tiny_mandate(max_price_deviation=0.01), market, state)
    assert ok(r, "PRICE_DEVIATION")
    r = evaluate(buy_x(1, price=101.01), tiny_mandate(max_price_deviation=0.01), market, state)
    assert not ok(r, "PRICE_DEVIATION")


def test_price_deviation_skipped_without_limit(ev):
    assert code_result(ev(limits={"max_price_deviation": None}, price=999.0), "PRICE_DEVIATION") is None


# --- 13 FUNDS ---


def test_funds_fail(ev):
    r = ev(ticker="LKOH", quantity=40, price=6812.0)
    assert (
        code_result(r, "FUNDS")["detail"] == "Не хватает денег: нужно 272 480 ₽, свободно 268 653 ₽. Плечо запрещено."
    )
    assert r["decision"] == "DENY"


def test_funds_pass(ev, t1):
    assert ok(ev(ticker="LKOH", quantity=1), "FUNDS")
    assert ok(ev(mandate={**t1, "forbid_leverage": False}, ticker="LKOH", quantity=40, price=6812.0), "FUNDS")
    assert ok(ev(mandate={**t1, "allow_sell": True}, side="sell", ticker="LKOH", quantity=17), "FUNDS")


# --- 14 WHITELIST ---


def test_whitelist_is_soft_by_default(ev):
    r = ev(ticker="YDEX", quantity=1, price=4121.0)
    check = code_result(r, "WHITELIST")
    assert (check["ok"], check["severity"]) == (False, "soft")
    assert check["detail"] == "Бумаги Яндекс нет в вашем списке."
    assert r["decision"] == "ASK"


def test_whitelist_is_hard_when_strict(ev, t1):
    r = ev(mandate={**t1, "strict_whitelist": True}, ticker="YDEX", quantity=1, price=4121.0)
    assert (code_result(r, "WHITELIST")["ok"], code_result(r, "WHITELIST")["severity"]) == (False, "hard")
    assert r["decision"] == "DENY"


def test_whitelist_pass(ev, t1):
    assert ok(ev(), "WHITELIST")
    assert ok(ev(mandate={**t1, "allowed_tickers": []}, ticker="YDEX", quantity=1, price=4121.0), "WHITELIST")


# --- 15 POSITION_SHARE ---


def test_position_share_fail(ev):
    r = ev(ticker="SBER", quantity=130, price=305.5)
    assert code_result(r, "POSITION_SHARE")["detail"] == "После покупки доля Сбербанк станет 16,19%, лимит 15%."
    assert r["decision"] == "DENY"


def test_position_share_pass_and_only_for_buys(ev, t1):
    assert ok(ev(ticker="SBER", quantity=50, price=305.5), "POSITION_SHARE")
    assert ok(ev(mandate={**t1, "allow_sell": True}, side="sell", ticker="SBER", quantity=400), "POSITION_SHARE")


def test_position_share_boundary_is_not_a_failure():
    assert ok(run_tiny(buy_x(5), max_position_share=0.15), "POSITION_SHARE")  # (100 + 50) / 1000 = 0.15
    assert not ok(run_tiny(buy_x(6), max_position_share=0.15), "POSITION_SHARE")


def test_position_share_skipped_without_limit(ev):
    assert code_result(ev(limits={"max_position_share": None}, ticker="SBER", quantity=130), "POSITION_SHARE") is None


# --- 16 ORDER_VALUE ---


def test_order_value_fail(ev):
    r = ev(ticker="LKOH", quantity=8, price=6812.0)
    assert code_result(r, "ORDER_VALUE")["detail"] == "Заявка на 54 496 ₽ больше лимита 50 000 ₽."
    assert r["decision"] == "DENY"


def test_order_value_pass_and_boundary(ev):
    assert ok(ev(ticker="LKOH", quantity=7, price=6812.0), "ORDER_VALUE")
    assert ok(run_tiny(buy_x(5), max_order_value=50), "ORDER_VALUE")  # ровно 50 - не больше
    assert not ok(run_tiny(buy_x(6), max_order_value=50), "ORDER_VALUE")


def test_order_value_skipped_without_limit(ev):
    assert code_result(ev(limits={"max_order_value": None}), "ORDER_VALUE") is None


# --- 17 DAILY_TURNOVER ---


def test_daily_turnover_fail(ev):
    r = ev(ticker="SBER", quantity=10, price=305.5, state={"turnover_today": 98000})
    assert code_result(r, "DAILY_TURNOVER")["detail"] == "Дневной лимит 100 000 ₽ будет превышен: уже 98 000 ₽."
    assert r["decision"] == "DENY"


def test_daily_turnover_pass_and_boundary(ev):
    assert ok(ev(ticker="SBER", quantity=10, price=305.5, state={"turnover_today": 0}), "DAILY_TURNOVER")
    assert ok(run_tiny(buy_x(6), daily_turnover=100, state={"turnover_today": 40}), "DAILY_TURNOVER")  # 40 + 60 = 100
    assert not ok(run_tiny(buy_x(7), daily_turnover=100, state={"turnover_today": 40}), "DAILY_TURNOVER")


def test_daily_turnover_skipped_without_limit(ev):
    assert code_result(ev(limits={"daily_turnover": None}, state={"turnover_today": 1e9}), "DAILY_TURNOVER") is None


# --- 18 ASK_THRESHOLD ---


def test_ask_threshold_fail(ev):
    r = ev(ticker="SU26248RMFS4", quantity=34, price=905.6)
    check = code_result(r, "ASK_THRESHOLD")
    assert (check["ok"], check["severity"]) == (False, "soft")
    assert check["detail"] == "Заявка на 30 790,40 ₽: крупные заявки по мандату подтверждаете вы."
    assert failed(r) == ["ASK_THRESHOLD"] and r["decision"] == "ASK"
    assert r["explanation"] == "Нужно ваше подтверждение: " + check["detail"]


def test_ask_threshold_pass_and_boundary(ev):
    assert ok(ev(ticker="SU26248RMFS4", quantity=10, price=905.6), "ASK_THRESHOLD")
    assert ok(run_tiny(buy_x(5), ask_above=50), "ASK_THRESHOLD")
    assert not ok(run_tiny(buy_x(6), ask_above=50), "ASK_THRESHOLD")


def test_ask_threshold_skipped_without_limit(ev):
    assert code_result(ev(limits={"ask_above": None}), "ASK_THRESHOLD") is None


# --- 19 BOOK_DEPTH ---


def test_book_depth_fail(ev):
    r = ev(ticker="SU26243RMFS4", quantity=40, price=746.4)
    check = code_result(r, "BOOK_DEPTH")
    assert (check["ok"], check["severity"]) == (False, "soft")
    assert check["detail"] == "Заявка забирает 66,7% видимого стакана: возможен сдвиг цены."
    assert failed(r) == ["BOOK_DEPTH"] and r["decision"] == "ASK"


def test_book_depth_boundary(ev):
    assert ok(ev(ticker="SU26243RMFS4", quantity=12, price=746.4), "BOOK_DEPTH")  # 12 лотов = 0.2 * 60
    assert not ok(ev(ticker="SU26243RMFS4", quantity=13, price=746.4), "BOOK_DEPTH")


def test_book_depth_uses_bids_for_sells(ev, t1):
    m = {**t1, "allow_sell": True}  # bids: 30 + 40 лотов, 0.2 * 70 = 14
    assert ok(ev(mandate=m, side="sell", ticker="SU26243RMFS4", quantity=14), "BOOK_DEPTH")
    assert not ok(ev(mandate=m, side="sell", ticker="SU26243RMFS4", quantity=15), "BOOK_DEPTH")


def test_book_depth_counts_only_one_percent_band(ev):
    book = {"asks": [[100.0, 10], [102.0, 1000]], "bids": [[99.0, 10]]}  # второй уровень дальше 1% и не считается
    market_patch = {"books": {"TMOS": book}}
    r = ev(ticker="TMOS", quantity=3, market_patch=market_patch)
    assert not ok(r, "BOOK_DEPTH")  # 3 > 0.2 * 10


def test_book_depth_skipped_without_book_or_limit(ev, market, t1):
    del market["books"]["TMOS"]
    r = evaluate(make_order(), t1, market, fresh_state(now=NOW))
    assert code_result(r, "BOOK_DEPTH") is None and r["decision"] == "ALLOW"
    del market["books"]
    assert code_result(evaluate(make_order(), t1, market, fresh_state(now=NOW)), "BOOK_DEPTH") is None
    assert code_result(ev(limits={"max_book_share": None}), "BOOK_DEPTH") is None


def test_book_depth_skipped_when_the_needed_side_is_empty(ev, t1):
    book = {"asks": [], "bids": [[7.05, 100]]}
    assert code_result(ev(market_patch={"books": {"TMOS": book}}), "BOOK_DEPTH") is None  # покупка: asks пусты
    m = {**t1, "allow_sell": True}
    r = ev(mandate=m, side="sell", ticker="TMOS", quantity=1000, market_patch={"books": {"TMOS": book}})
    assert not ok(r, "BOOK_DEPTH")  # продажа смотрит в bids: 1000 > 0.2 * 100


# --- решение, пояснения, структура ---


def test_all_checks_in_spec_order_for_allow(ev):
    r = ev()
    assert [c["code"] for c in r["checks"]] == list(CHECK_CODES) and len(CHECK_CODES) == 19
    assert r["decision"] == "ALLOW" and r["actions"] == []
    assert r["explanation"] == "В рамках мандата: заявка отправлена брокеру."
    assert all(c["ok"] and c["detail"] for c in r["checks"])


def test_stop_checks_are_the_first_five():
    assert STOP_CODES == CHECK_CODES[:5] == ("KILL", "PAUSED", "DUPLICATE", "RUNAWAY", "INSTRUMENT")


def test_severities(ev):
    r = ev()
    soft = {c["code"] for c in r["checks"] if c["severity"] == "soft"}
    assert soft == {"WHITELIST", "ASK_THRESHOLD", "BOOK_DEPTH"}


def test_hard_failure_beats_soft_in_decision_and_explanation(ev):
    r = ev(ticker="SU52002RMFS1", quantity=1, price=984.5)  # CURRENCY (hard) и WHITELIST (soft)
    assert failed(r) == ["CURRENCY", "WHITELIST"]
    assert r["decision"] == "DENY" and r["explanation"] == "Отклонено: Валюта CNY не разрешена мандатом."


def test_explanation_quotes_first_soft_failure(ev):
    r = ev(ticker="YDEX", quantity=10, price=4121.0)  # WHITELIST и ASK_THRESHOLD, оба мягкие
    assert failed(r) == ["WHITELIST", "ASK_THRESHOLD"]
    assert r["explanation"] == "Нужно ваше подтверждение: Бумаги Яндекс нет в вашем списке."


def test_order_value_is_rounded_to_cents(ev):
    assert ev(ticker="SU26248RMFS4", quantity=4, price=905.6)["order_value"] == 3622.4
    assert ev(ticker="TMOS", quantity=1418)["order_value"] == 9996.9
    assert ev(ticker="TMOS", quantity=1, price=7.0549)["order_value"] == 7.05


def test_market_order_is_valued_at_last_price(ev):
    r = ev(ticker="SBER", quantity=10, price=None)
    assert r["order_value"] == 3054.0


def test_free_mandate_skips_limit_checks_and_allows_everything(ev, t2):
    r = ev(mandate=t2, ticker="SiZ6", quantity=10, price=84510.0)
    assert [c["code"] for c in r["checks"]] == [
        "KILL", "PAUSED", "DUPLICATE", "INSTRUMENT", "TRADING_STATUS", "CLASS", "QUAL_ONLY",
        "CURRENCY", "LOT", "SIDE", "FUNDS", "WHITELIST",
    ]  # fmt: skip
    assert r["decision"] == "ALLOW"
    assert ev(mandate=t2, side="sell", ticker="SBER", quantity=400)["decision"] == "ALLOW"


def test_evaluate_does_not_mutate_inputs(market, t1):
    order, state = make_order(ticker="SBER", quantity=10, order_id="A"), fresh_state(now=NOW, recent_order_ts=[NOW])
    before = copy.deepcopy((order, t1, market, state))
    evaluate(order, t1, market, state)
    assert (order, t1, market, state) == before


def test_evaluate_is_deterministic(market, t1):
    args = (make_order(ticker="SBER", quantity=130, price=305.5), t1, market, fresh_state(now=NOW))
    assert evaluate(*args) == evaluate(*args)


def test_evaluate_accepts_dataclasses(market, t1):
    order = Order.parse(make_order(ticker="SBER", quantity=10, price=305.5))
    r = evaluate(order, Mandate.from_dict(t1), market, fresh_state(now=NOW))
    assert r["decision"] == "ALLOW"


def test_evaluate_view_equals_evaluate(market, t1):
    order, state = make_order(ticker="SU52002RMFS1", quantity=1, price=984.5), fresh_state(now=NOW)
    assert evaluate_view(order, t1, build_view(order, market), state) == evaluate(order, t1, market, state)


def test_view_matches_spec_derivations(market):
    view = build_view(make_order(ticker="TMOS"), market)
    assert view["total_value"] == pytest.approx(1_000_000)
    assert view["position_value"] == pytest.approx(19000 * 7.05)
    assert view["position_qty"] == 19000 and view["fx"] == 1
    assert build_view(make_order(ticker="NOPE"), market)["instrument"] is None
