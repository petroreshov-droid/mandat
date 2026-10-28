"""Модели, форматирование чисел, часы."""

from datetime import UTC, datetime

import pytest
from conftest import assert_no_long_dashes

from mandat.canonical import sha256_hex
from mandat.clock import SimClock, fmt_time, parse_iso
from mandat.explain import fmt_num, fmt_pct, fmt_rub, mandate_rules, plural
from mandat.fixtures import all_mandates, demo_mandate, load_mandate, load_market
from mandat.models import GatewayState, Mandate, Order, OrderFormatError, fresh_state

# --- Order ---


def test_order_parse_minimal_and_to_dict_keys():
    order = Order.parse({"ticker": " SBER ", "side": "buy", "quantity": 10.0})
    assert order.to_dict() == {
        "order_id": None, "ticker": "SBER", "side": "buy", "quantity": 10,
        "price": None, "agent": "agent", "reason": "",
    }  # fmt: skip
    assert isinstance(order.quantity, int)


@pytest.mark.parametrize(
    "raw",
    [
        {"ticker": "", "side": "buy", "quantity": 1},
        {"ticker": None, "side": "buy", "quantity": 1},
        {"ticker": "SBER", "side": "hold", "quantity": 1},
        {"ticker": "SBER", "side": "buy", "quantity": "10"},
        {"ticker": "SBER", "side": "buy", "quantity": True},
        {"ticker": "SBER", "side": "buy", "quantity": float("nan")},
        {"ticker": "SBER", "side": "buy", "quantity": 1, "price": 0},
        {"ticker": "SBER", "side": "buy", "quantity": 1, "price": -5},
        {"ticker": "SBER", "side": "buy", "quantity": 1, "price": "305"},
        {"ticker": "SBER", "side": "buy", "quantity": 1, "order_id": ""},
        {"ticker": "SBER", "side": "buy", "quantity": 1, "reason": 5},
    ],
)
def test_order_parse_rejects_garbage(raw):
    with pytest.raises(OrderFormatError):
        Order.parse(raw)


def test_order_parse_keeps_fractional_quantity_for_the_lot_check_and_caps_reason():
    assert Order.parse({"ticker": "A", "side": "sell", "quantity": 2.5}).quantity == 2.5
    assert len(Order.parse({"ticker": "A", "side": "buy", "quantity": 1, "reason": "я" * 5000}).reason) == 1000


# --- Mandate ---


@pytest.mark.parametrize("name", ["t1_bonds", "t2_free"])
def test_mandate_roundtrip_is_exact_and_hash_is_stable(name):
    raw = load_mandate(name)
    mandate = Mandate.from_dict(raw)
    assert mandate.to_dict() == raw
    assert mandate.hash == sha256_hex(raw)


def test_mandate_from_dict_names_the_missing_field():
    raw = load_mandate("t1_bonds")
    del raw["allow_sell"]
    with pytest.raises(ValueError, match="allow_sell"):
        Mandate.from_dict(raw)


def test_demo_mandate_differs_from_t1_only_in_daily_turnover():
    t1, demo = load_mandate("t1_bonds"), demo_mandate()
    assert demo["limits"]["daily_turnover"] == 100000 and t1["limits"]["daily_turnover"] is None
    demo["limits"]["daily_turnover"] = None
    assert demo == t1
    assert set(all_mandates()) == {"t1_bonds", "t2_free"}


def test_state_shares_lists_with_engine_dict():
    st = GatewayState(now=5.0)
    view = st.as_dict()
    st.recent_order_ts.append(1.0)
    assert view["recent_order_ts"] == [1.0]
    assert set(view) == {
        "now",
        "turnover_today",
        "recent_order_ts",
        "seen_order_ids",
        "violation_ts",
        "paused",
        "kill_switch",
    }
    assert fresh_state(paused=True)["paused"] is True


# --- числа и тексты ---


def test_number_formatting():
    assert fmt_num(1234567.5) == "1 234 567,5"
    assert fmt_num(-0.001) == "0"
    assert fmt_num(905.6, 4) == "905,6"
    assert fmt_rub(4200) == "4 200 ₽"
    assert fmt_rub(3622.4) == "3 622,40 ₽"
    assert fmt_rub(272480.0) == "272 480 ₽"
    assert fmt_pct(0.15) == "15%"
    assert fmt_pct(0.0055) == "0,55%"
    assert fmt_pct(0.07) == "7%"


def test_plural():
    assert [plural(n, "запись", "записи", "записей") for n in (1, 2, 5, 11, 21, 54)] == [
        "1 запись", "2 записи", "5 записей", "11 записей", "21 запись", "54 записи",
    ]  # fmt: skip


def test_mandate_rules_in_plain_russian():
    market = load_market()
    names = {t: i["name"] for t, i in market["instruments"].items()}
    rules = mandate_rules(demo_mandate(), names)
    text = "\n".join(rules)
    assert "Можно торговать: облигации, фонды, акции." in rules
    assert "Продажи запрещены." in rules
    assert "Не больше 15% портфеля в одной бумаге." in rules
    assert "Оборот за день - не больше 100 000 ₽." in rules
    assert "Сбербанк" in text and "Лукойл" in text
    assert "10-е число докупается Т-Капитал Индекс МосБиржи на 10 000 ₽" in text
    assert_no_long_dashes(text)


def test_mandate_rules_for_free_mandate():
    text = "\n".join(mandate_rules(load_mandate("t2_free")))
    assert "Списка бумаг нет" in text and "Плечо разрешено." in text and "Продажи разрешены." in text


# --- часы ---


def test_sim_clock():
    clock = SimClock(100.0)
    assert clock.advance(0.25) == 100.25 and clock.now() == 100.25
    assert clock.set(7) == 7.0


def test_parse_iso_and_fmt_time_use_moscow_time():
    ts = parse_iso("2026-11-10T10:00:00+03:00")
    assert ts == datetime(2026, 11, 10, 7, 0, tzinfo=UTC).timestamp()
    assert fmt_time(ts) == "10:00:00"
    assert fmt_time(ts + 0.25) == "10:00:00.250"
    assert fmt_time(ts, ms=True) == "10:00:00.000"


def test_missing_data_dir_gives_a_helpful_error(tmp_path, monkeypatch):
    from mandat import fixtures

    monkeypatch.setattr(fixtures, "DATA_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="MANDAT_DATA_DIR"):
        fixtures.load_market()
