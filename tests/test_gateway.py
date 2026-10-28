import copy
from datetime import datetime

import pytest
from conftest import NOW

from mandat.brokers import Broker, BrokerError, MockBroker
from mandat.clock import MSK, SimClock
from mandat.fixtures import demo_mandate, load_mandate
from mandat.gateway import Gateway, GatewayError, MandateRejected

BAD_LOT = {"ticker": "SBER", "side": "buy", "quantity": 15}  # всегда DENY (LOT)
GOOD = {"ticker": "SBER", "side": "buy", "quantity": 10, "price": 305.5}  # 3 055 ₽, ALLOW


def codes(result):
    return [c["code"] for c in result["checks"] if not c["ok"]]


# --- ALLOW / DENY / состояние ---


def test_allow_sends_to_broker_and_updates_state(gw, broker):
    r = gw.post_order(**GOOD, order_id="A1")
    assert r["decision"] == "ALLOW" and r["broker"] == {"order_id": "MOCK-000001", "status": "filled"}
    assert r["pending_id"] is None and r["paused"] is False
    assert gw.state.turnover_today == 3055.0
    assert gw.state.recent_order_ts == [NOW] and gw.state.seen_order_ids == ["A1"]
    snap = broker.snapshot()
    assert snap["positions"]["SBER"] == 410 and snap["cash_rub"] == pytest.approx(268653 - 3055)


def test_deny_never_reaches_the_broker_and_counts_as_violation(gw, broker):
    r = gw.post_order(**BAD_LOT)
    assert r["decision"] == "DENY" and codes(r) == ["LOT"] and r["broker"] is None
    assert broker.orders == [] and gw.state.turnover_today == 0
    assert gw.state.violation_ts == [NOW] and gw.state.recent_order_ts == [NOW]


def test_result_carries_engine_output(gw):
    r = gw.post_order(**GOOD)
    assert set(r) >= {
        "decision",
        "checks",
        "explanation",
        "order_value",
        "actions",
        "pending_id",
        "broker",
        "seq",
        "paused",
    }
    assert r["order_value"] == 3055.0 and r["explanation"] == "В рамках мандата: заявка отправлена брокеру."


def test_market_order_executes_at_last_price(gw, broker):
    gw.post_order("TMOS", "buy", 1418)
    assert broker.orders[0]["exec_price"] == 7.05
    assert gw.state.turnover_today == 9996.9


# --- три отказа и пауза ---


def test_three_denials_in_ten_minutes_pause_the_agent(gw, clock, broker):
    results = []
    for _ in range(3):
        clock.advance(1)
        results.append(gw.post_order(**BAD_LOT))
    assert [r["paused"] for r in results] == [False, False, True]
    clock.advance(1)
    r = gw.post_order(**GOOD)
    assert r["decision"] == "DENY" and codes(r) == ["PAUSED"] and broker.orders == []


@pytest.mark.parametrize(("third_at", "paused"), [(599, True), (600, False)])
def test_strike_window_is_strict_600_seconds(gw, clock, third_at, paused):
    for at in (0, 300, third_at):
        clock.set(NOW + at)
        r = gw.post_order(**BAD_LOT)
    assert r["paused"] is paused


def test_allowed_orders_are_not_strikes(gw, clock):
    for _ in range(4):
        clock.advance(20)
        gw.post_order(**GOOD)
    assert gw.state.violation_ts == [] and gw.state.paused is False


def test_runaway_robot_is_paused_after_five_allowed(gw, clock, broker):
    decisions = []
    for _ in range(40):
        clock.advance(0.25)
        decisions.append(gw.post_order("SU26238RMFS4", "buy", 2, 612.5))
    assert [d["decision"] for d in decisions[:5]] == ["ALLOW"] * 5
    assert codes(decisions[5]) == ["RUNAWAY"] and decisions[5]["actions"] == ["PAUSE"] and decisions[5]["paused"]
    assert all(codes(d) == ["PAUSED"] for d in decisions[6:])
    assert len(broker.orders) == 5


def test_resume_clears_pause_and_violations(gw, clock):
    for _ in range(3):
        clock.advance(1)
        gw.post_order(**BAD_LOT)
    assert gw.state.paused
    r = gw.resume()
    assert r["paused"] is False and gw.state.violation_ts == []
    clock.advance(1)
    assert gw.post_order(**GOOD)["decision"] == "ALLOW"
    clock.advance(1)
    gw.post_order(**BAD_LOT)
    assert gw.state.paused is False  # счёт отказов пошёл заново


def test_kill_switch_blocks_everything_until_switched_off(gw, clock, broker):
    gw.kill_switch(True)
    r = gw.post_order(**GOOD)
    assert codes(r) == ["KILL"] and r["explanation"] == "Отклонено: Агент выключен клиентом."
    gw.kill_switch(False)
    clock.advance(1)
    assert gw.post_order(**GOOD)["decision"] == "ALLOW"
    assert [r["type"] for r in gw.journal.records if r["type"] == "kill"] == ["kill", "kill"]
    assert len(broker.orders) == 1


def test_denials_under_kill_switch_also_count_and_need_resume(gw, clock):
    gw.kill_switch(True)
    for _ in range(3):
        clock.advance(1)
        gw.post_order(**GOOD)
    gw.kill_switch(False)
    clock.advance(1)
    assert codes(gw.post_order(**GOOD)) == ["PAUSED"]
    gw.resume()
    clock.advance(1)
    assert gw.post_order(**GOOD)["decision"] == "ALLOW"


# --- идемпотентность ---


def test_same_order_id_is_executed_once(gw, broker, clock):
    first = gw.post_order(**GOOD, order_id="X-1")
    clock.advance(1)
    second = gw.post_order(**GOOD, order_id="X-1")
    assert first["decision"] == "ALLOW" and second["decision"] == "DENY" and codes(second) == ["DUPLICATE"]
    assert second["explanation"] == "Отклонено: Повтор заявки X-1: она уже обработана, вторая не отправлена."
    assert len(broker.orders) == 1 and gw.state.turnover_today == 3055.0
    assert gw.state.seen_order_ids == ["X-1"]


def test_orders_without_id_are_never_duplicates(gw, broker, clock):
    for _ in range(2):
        clock.advance(1)
        assert gw.post_order(**GOOD)["decision"] == "ALLOW"
    assert len(broker.orders) == 2


# --- ASK, подтверждение, отказ ---

YDEX = {"ticker": "YDEX", "side": "buy", "quantity": 1, "price": 4121.0}


def test_ask_waits_for_the_client(gw, broker):
    r = gw.post_order(**YDEX)
    assert r["decision"] == "ASK" and codes(r) == ["WHITELIST"] and r["pending_id"] == "P0001"
    assert broker.orders == [] and gw.state.turnover_today == 0 and gw.state.violation_ts == []
    assert [p["pending_id"] for p in gw.pending_orders()] == ["P0001"]


def test_confirm_sends_the_order(gw, broker):
    pid = gw.post_order(**YDEX)["pending_id"]
    r = gw.confirm(pid)
    assert r["decision"] == "ALLOW" and r["broker"]["status"] == "filled"
    assert r["explanation"] == "Подтверждено клиентом: заявка отправлена брокеру."
    assert gw.state.turnover_today == 4121.0 and len(broker.orders) == 1
    assert gw.pending_orders() == []
    assert gw.journal.records[-1]["type"] == "confirm"


def test_confirm_rechecks_hard_rules_on_a_fresh_snapshot(gw, broker):
    pid = gw.post_order(**YDEX)["pending_id"]
    broker.credit_cash(-268_000)  # пока клиент думал, деньги ушли
    r = gw.confirm(pid)
    assert r["decision"] == "DENY" and "FUNDS" in codes(r) and broker.orders == []
    assert r["explanation"].startswith("Подтверждение не сработало: Не хватает денег")
    assert [p["pending_id"] for p in gw.pending_orders()] == [pid]  # заявка осталась, можно повторить
    broker.credit_cash(268_000)
    assert gw.confirm(pid)["decision"] == "ALLOW"


def test_confirm_ignores_soft_failures(gw, broker):
    pid = gw.post_order("SU26243RMFS4", "buy", 40, 746.4)["pending_id"]  # BOOK_DEPTH, мягкая
    r = gw.confirm(pid)
    assert r["decision"] == "ALLOW" and any(c["code"] == "BOOK_DEPTH" and not c["ok"] for c in r["checks"])


def test_confirm_uses_fresh_state_not_the_state_of_the_ask(gw, clock):
    pid = gw.post_order(**YDEX, order_id="Y-1")["pending_id"]
    r = gw.confirm(pid)  # свой order_id не должен дать DUPLICATE
    assert r["decision"] == "ALLOW"


def test_confirm_does_not_count_the_pending_order_as_a_runaway_attempt(broker, clock):
    mandate = demo_mandate()
    mandate["limits"]["max_orders_per_minute"] = 1
    gw = Gateway(mandate, broker, clock=clock)
    pid = gw.post_order(**YDEX)["pending_id"]
    assert gw.state.recent_order_ts == [NOW]
    assert gw.confirm(pid)["decision"] == "ALLOW"
    assert gw.state.recent_order_ts == [NOW]  # подтверждение - не новая попытка агента


def test_confirm_is_refused_while_paused_or_killed(gw, clock):
    pid = gw.post_order(**YDEX)["pending_id"]
    gw.kill_switch(True)
    assert codes(gw.confirm(pid)) == ["KILL"]
    gw.kill_switch(False)
    assert gw.confirm(pid)["decision"] == "ALLOW"


def test_confirm_checks_against_the_current_mandate(gw, t1):
    pid = gw.post_order(**YDEX)["pending_id"]
    strict = copy.deepcopy(t1)
    strict["strict_whitelist"] = True  # клиент подписал новую версию: вне списка теперь запрещено
    gw.sign_mandate(strict)
    r = gw.confirm(pid)
    assert r["decision"] == "DENY" and "WHITELIST" in codes(r)


def test_reject_is_journaled_and_final(gw, broker):
    pid = gw.post_order(**YDEX)["pending_id"]
    r = gw.reject(pid, "не хочу")
    assert r["status"] == "rejected" and gw.pending_orders() == []
    last = gw.journal.records[-1]
    assert last["type"] == "reject" and last["payload"]["reason"] == "не хочу" and last["payload"]["pending_id"] == pid
    with pytest.raises(GatewayError):
        gw.confirm(pid)
    assert broker.orders == []


def test_unknown_and_repeated_confirmations_raise(gw):
    with pytest.raises(GatewayError):
        gw.confirm("P9999")
    with pytest.raises(GatewayError):
        gw.reject("P9999")
    pid = gw.post_order(**YDEX)["pending_id"]
    gw.confirm(pid)
    with pytest.raises(GatewayError):
        gw.confirm(pid)


# --- смена календарного дня ---


def test_daily_turnover_resets_on_new_calendar_day(gw, clock):
    clock.set(datetime(2026, 11, 10, 23, 59, 50, tzinfo=MSK).timestamp())
    gw.post_order(**GOOD)
    assert gw.state.turnover_today == 3055.0
    clock.advance(5)  # 23:59:55, тот же день
    gw.post_order(**GOOD)
    assert gw.state.turnover_today == 6110.0
    clock.advance(10)  # 00:00:05 следующего дня
    gw.post_order(**GOOD)
    assert gw.state.turnover_today == 3055.0


def test_daily_limit_is_available_again_the_next_day(gw, clock):
    gw.state.turnover_today = 99_000.0
    assert codes(gw.post_order(**GOOD)) == ["DAILY_TURNOVER"]
    clock.advance(24 * 3600)
    assert gw.post_order(**GOOD)["decision"] == "ALLOW"


# --- смена мандата ---


def test_agent_cannot_change_the_mandate(gw):
    before = gw.mandate_hash
    r = gw.update_mandate({"allow_sell": True}, reason="так удобнее")
    assert r["decision"] == "DENY" and codes(r) == ["MANDATE_CHANGE"] and r["checks"][0]["severity"] == "hard"
    assert r["explanation"] == "Отклонено: Мандат меняет только клиент через подпись новой версии."
    assert gw.mandate_hash == before and gw.mandate["allow_sell"] is False
    assert gw.state.violation_ts == [NOW]


def test_repeated_mandate_edits_pause_the_agent(gw, clock):
    for _ in range(3):
        clock.advance(1)
        r = gw.update_mandate({"limits": {"max_position_share": 1}})
    assert r["paused"] is True
    clock.advance(1)
    assert codes(gw.post_order(**GOOD)) == ["PAUSED"]


def test_mandate_edits_are_not_order_attempts(gw):
    gw.update_mandate({"x": 1})
    assert gw.state.recent_order_ts == []


def test_sign_new_version_changes_hash_and_journal(gw, t1, clock):
    old = gw.mandate_hash
    newer = copy.deepcopy(t1)
    newer["limits"]["ask_above"] = 5000
    result = gw.sign_mandate(newer)
    assert result["hash"] == gw.mandate_hash != old
    signed = [r for r in gw.journal.records if r["type"] == "mandate_signed"]
    assert len(signed) == 2 and signed[1]["payload"]["mandate_hash"] == gw.mandate_hash
    clock.advance(1)
    r = gw.post_order("SBER", "buy", 20, 305.5)  # 6 110 ₽ > 5 000 ₽ - теперь ASK
    assert codes(r) == ["ASK_THRESHOLD"]
    assert gw.journal.records[-1]["payload"]["mandate_hash"] == gw.mandate_hash


def test_mandate_with_critical_findings_cannot_be_signed(broker, clock, gw):
    with pytest.raises(MandateRejected) as exc:
        gw.sign_mandate(load_mandate("t2_free"))
    assert {f["code"] for f in exc.value.findings} == {"L_NO_RUNAWAY", "L_LEVERAGE", "L_DERIVATIVES"}
    with pytest.raises(MandateRejected):
        Gateway(load_mandate("t2_free"), broker, clock=clock)
    assert gw.mandate == demo_mandate()  # прежний мандат остался


def test_structurally_invalid_mandate_cannot_be_signed(gw, t1):
    broken = copy.deepcopy(t1)
    broken["allowed_classes"] = ["crypto"]
    with pytest.raises(MandateRejected) as exc:
        gw.sign_mandate(broken)
    assert "allowed_classes" in exc.value.problems[0]


def test_warnings_do_not_block_signing(broker, clock):
    gw = Gateway(load_mandate("t1_bonds"), broker, clock=clock)  # L_NO_DAILY - только предупреждение
    assert gw.journal.records[0]["payload"]["lint"] == ["L_NO_DAILY"]


# --- кривые заявки и сбои брокера ---


@pytest.mark.parametrize(
    "args",
    [
        {"ticker": "SBER", "side": "hold", "quantity": 10},
        {"ticker": "SBER", "side": "buy", "quantity": "10"},
        {"ticker": "SBER", "side": "buy", "quantity": 10, "price": 0},
        {"ticker": "", "side": "buy", "quantity": 10},
    ],
)
def test_malformed_orders_are_denied_without_touching_engine_or_broker(gw, broker, args):
    r = gw.post_order(**args)
    assert r["decision"] == "DENY" and codes(r) == ["ORDER_FORMAT"] and broker.orders == []
    assert gw.state.violation_ts == [NOW]
    rec = gw.journal.records[-1]
    assert rec["payload"]["kind"] == "malformed" and gw.journal.replay(rec["seq"])["match"]


class RaisingBroker(MockBroker):
    def place_order(self, order):
        raise BrokerError("нет связи")


class RejectingBroker(MockBroker):
    def place_order(self, order):
        return {"order_id": "R-1", "status": "rejected", "reason": "лимит брокера"}


def test_broker_failure_does_not_crash_or_count_as_turnover(t1, clock):
    gw = Gateway(t1, RaisingBroker(), clock=clock)
    r = gw.post_order(**GOOD)
    assert r["decision"] == "ALLOW" and r["broker"]["status"] == "error" and "нет связи" in r["broker"]["reason"]
    assert gw.state.turnover_today == 0


def test_broker_rejection_is_reported_and_not_counted(t1, clock):
    gw = Gateway(t1, RejectingBroker(), clock=clock)
    r = gw.post_order(**GOOD)
    assert r["broker"] == {"order_id": "R-1", "status": "rejected", "reason": "лимит брокера"}
    assert gw.state.turnover_today == 0


# --- журнал решений ---


def test_every_decision_is_journaled_with_state_before(gw, clock):
    gw.post_order(**GOOD)
    clock.advance(1)
    gw.post_order(**BAD_LOT)
    ok_rec, bad_rec = gw.journal.records[1], gw.journal.records[2]
    assert ok_rec["type"] == "decision" and ok_rec["ts"] == NOW
    assert ok_rec["payload"]["state_before"] == {
        "turnover_today": 0.0, "orders_last_minute": 0, "paused": False, "kill_switch": False, "order_id_seen": False,
    }  # fmt: skip
    assert ok_rec["payload"]["broker"] == {"order_id": "MOCK-000001", "status": "filled"}
    assert bad_rec["payload"]["state_before"]["turnover_today"] == 3055.0
    assert bad_rec["payload"]["state_before"]["orders_last_minute"] == 1
    assert bad_rec["payload"]["broker"] is None and bad_rec["payload"]["decision"] == "DENY"
    assert gw.journal.verify() is None


def test_evaluator_is_injectable_and_gets_the_exact_inputs(t1, broker, clock):
    seen = []

    def spy(order, mandate, market, state):
        seen.append((copy.deepcopy(order), copy.deepcopy(state)))
        from mandat.rules import evaluate

        return evaluate(order, mandate, market, state)

    gw = Gateway(t1, broker, clock=clock, evaluate=spy)
    gw.post_order(**GOOD, order_id="Z")
    assert seen[0][0]["order_id"] == "Z" and seen[0][1]["now"] == NOW and seen[0][1]["recent_order_ts"] == []


# --- чтение для агента ---


def test_portfolio_tool(gw):
    p = gw.get_portfolio()
    assert p["cash_rub"] == 268653.0 and p["total_value_rub"] == 1_000_000.0
    sber = next(x for x in p["positions"] if x["ticker"] == "SBER")
    assert sber["quantity"] == 400 and sber["share"] == 0.1222


def test_instrument_and_orderbook_tools(gw):
    assert gw.get_instrument("SBER")["lot"] == 10
    assert "не найдена" in gw.get_instrument("NOPE")["error"]
    assert gw.get_orderbook("SBER")["asks"][0] == [305.5, 8000]
    assert "error" in gw.get_orderbook("NOPE")


def test_mandate_tool_explains_rules_and_status(gw):
    m = gw.get_mandate()
    assert m["hash"] == gw.mandate_hash and m["title"] == "Облигации, фонд и реинвест"
    assert "Продажи запрещены." in m["rules"]
    assert m["status"] == {"paused": False, "kill_switch": False, "turnover_today_rub": 0.0, "daily_left_rub": 100000}
    gw.post_order(**GOOD)
    assert gw.get_mandate()["status"]["daily_left_rub"] == 100000 - 3055


def test_simulated_clock_is_used_not_wall_clock(t1, broker):
    clock = SimClock(NOW)
    gw = Gateway(t1, broker, clock=clock)
    gw.post_order(**GOOD)
    assert gw.journal.records[-1]["ts"] == NOW


def test_broker_interface_is_abstract():
    with pytest.raises(TypeError):
        Broker()


def test_mandate_tool_returns_a_copy_the_agent_cannot_use_to_edit_the_mandate(gw):
    gw.get_mandate()["mandate"]["allow_sell"] = True
    assert gw.mandate["allow_sell"] is False
