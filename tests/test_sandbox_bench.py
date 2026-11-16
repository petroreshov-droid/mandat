"""Части прогонов на песочнице, которые можно проверить без сети."""

from bench.sandbox_gateway import _busy_ms, to_md
from bench.sandbox_llm import sandbox_system
from bench.sandbox_scenario import sandbox_mandate
from mandat.brokers import MockBroker
from mandat.clock import SimClock
from mandat.fixtures import demo_start
from mandat.gateway import Gateway


def test_sandbox_mandate_fixes_the_ofz_ticker_and_moves_dca_off_the_fund():
    m = sandbox_mandate()
    assert "SU26248RMFS3" in m["allowed_tickers"] and "SU26248RMFS4" not in m["allowed_tickers"]
    reinvest, dca = m["standing_orders"]
    assert "SU26248RMFS3" in reinvest["from"] and dca["ticker"] == "SBER"
    Gateway(m, MockBroker(), clock=SimClock(demo_start()))  # мандат проходит стресс-тест и подписывается


def test_sandbox_prompt_matches_the_sandbox_mandate():
    text = sandbox_system()
    assert "SU26248RMFS3" in text and "SU26248RMFS4" not in text
    assert "докупать Сбербанк (SBER) на 10 000 ₽" in text and "докупать TMOS" not in text


def test_busy_time_merges_parallel_calls():
    calls = [("a", 0, 0.0, 1.0), ("b", 0, 0.5, 1.5), ("c", 0, 2.0, 2.5)]
    assert _busy_ms(calls) == 2000.0


def test_report_counts_placed_orders_separately():
    rep = {
        "meta": {"mandate": "t1"},
        "summary": {
            "orders": 1,
            "as_expected": 1,
            "violations": 1,
            "violations_sent_via_mandat": 0,
            "violations_executed_direct": 1,
            "latency_ms_p50": 1000,
            "latency_ms_max": 1000,
            "gateway_ms_max": 2,
        },
        "rows": [
            {"id": "G04", "what": "Продажа", "decision": "DENY", "failed": ["SIDE"], "broker": None, "ms_total": 900}
        ],
        "direct": [{"id": "G04", "status": "placed", "reason": None}],
    }
    md = to_md(rep)
    assert "исполнил сразу 0, выставил в стакан 1" in md and "| G04 | Продажа | DENY | SIDE | placed | 900 |" in md
