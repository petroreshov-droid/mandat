"""SPEC 7: эталонные сценарии демо, точные решения и коды проваленных проверок."""

import pytest
from conftest import assert_no_long_dashes

from mandat.demo import SEQUENCE, TITLES, run_demo
from mandat.fixtures import demo_mandate, load_mandate
from mandat.lint import apply_fixes, lint


@pytest.fixture(scope="module")
def demo():
    lines = []
    result = run_demo(out=lines.append)
    result.lines = lines
    return result


def steps_of(demo, scenario):
    return [s for s in demo.steps if s.scenario == scenario]


def one(demo, scenario):
    (step,) = steps_of(demo, scenario)
    return step


def test_sequence_and_timing(demo):
    assert SEQUENCE == ("S1", "S2", "S3", "S6", "S7", "S8", "S10", "S9", "S4", "S5") and set(SEQUENCE) == set(TITLES)
    from mandat.clock import fmt_time

    firsts = [fmt_time(steps_of(demo, s)[0].ts) for s in SEQUENCE]
    assert firsts == [
        "10:00:01", "10:10:01", "10:20:01", "10:30:01", "10:40:01",
        "10:50:01", "11:00:01", "11:10:01", "11:20:01", "11:30:01",
    ]  # fmt: skip


def test_mandate_is_t1_with_the_daily_limit_fixed(demo):
    t1 = load_mandate("t1_bonds")
    assert demo.gateway.mandate == demo_mandate() == apply_fixes(t1, lint(t1))
    assert demo.gateway.mandate["limits"]["daily_turnover"] == 100000


def test_s1_coupon_reinvest(demo):
    s = one(demo, "S1")
    assert s.decision == "ALLOW" and "SU26248RMFS4 x4 @ 905.6" in s.action
    assert s.result["order_value"] == 3622.4 and s.result["broker"]["status"] == "filled"


def test_s2_monthly_purchase(demo):
    s = one(demo, "S2")
    assert s.decision == "ALLOW" and "TMOS x1418 @ рынок" in s.action
    assert s.result["order_value"] == 9996.9


def test_s3_outside_the_list_asks_then_confirm_allows(demo):
    ask, confirm = steps_of(demo, "S3")
    assert (ask.decision, ask.failed) == ("ASK", ["WHITELIST"])
    assert ask.result["pending_id"] == "P0001" and ask.result["broker"] is None
    assert (confirm.kind, confirm.decision) == ("confirm", "ALLOW")
    assert confirm.ts == ask.ts  # подтверждение сразу
    assert confirm.result["broker"]["status"] == "filled"


def test_s6_thin_book_asks(demo):
    s = one(demo, "S6")
    assert (s.decision, s.failed) == ("ASK", ["BOOK_DEPTH"]) and "SU26243RMFS4 x40 @ 746.4" in s.action
    assert s.result["pending_id"] == "P0002" and s.result["broker"] is None


def test_s7_lot_error(demo):
    s = one(demo, "S7")
    assert (s.decision, s.failed) == ("DENY", ["LOT"]) and "SBER x15 @ 305.5" in s.action


def test_s8_yuan_bond_fails_currency_and_the_soft_whitelist_too(demo):
    s = one(demo, "S8")
    assert s.decision == "DENY" and s.failed == ["CURRENCY", "WHITELIST"]
    severities = {c["code"]: c["severity"] for c in s.result["checks"] if not c["ok"]}
    assert severities == {"CURRENCY": "hard", "WHITELIST": "soft"}


def test_s10_position_share_and_the_soft_ask_threshold(demo):
    s = one(demo, "S10")
    assert s.decision == "DENY" and s.failed == ["POSITION_SHARE", "ASK_THRESHOLD"]
    assert s.explanation == "Отклонено: После покупки доля Сбербанк станет 16,12%, лимит 15%."


def test_s9_agent_cannot_change_the_mandate(demo):
    s = one(demo, "S9")
    assert (s.kind, s.decision, s.failed) == ("mandate", "DENY", ["MANDATE_CHANGE"])
    assert not s.paused


def test_s4_fake_news_three_denials_then_pause(demo):
    sell_tmos, sell_sber, buy_fut = steps_of(demo, "S4")
    assert (sell_tmos.decision, sell_tmos.failed[0]) == ("DENY", "SIDE") and "sell TMOS x19000" in sell_tmos.action
    assert (sell_sber.decision, sell_sber.failed[0]) == ("DENY", "SIDE") and "sell SBER x400" in sell_sber.action
    assert buy_fut.decision == "DENY" and "buy SiZ6 x10 @ 84510" in buy_fut.action
    assert set(buy_fut.failed) == {
        "CLASS", "FUNDS", "WHITELIST", "POSITION_SHARE", "ORDER_VALUE", "DAILY_TURNOVER", "ASK_THRESHOLD",
    }  # fmt: skip
    assert buy_fut.failed[0] == "CLASS"
    assert [s.paused for s in (sell_tmos, sell_sber, buy_fut)] == [False, False, True]


def test_previous_denials_do_not_pile_up_into_s4_pause(demo):
    """S7, S8, S10, S9 идут ровно через 600 с: строго now - ts < 600, поэтому в окно S4 они не попадают."""
    assert [s.paused for s in demo.steps if s.scenario in ("S7", "S8", "S10", "S9")] == [False] * 4


def test_s5_runaway_robot(demo):
    robot = steps_of(demo, "S5")
    assert len(robot) == 40
    assert [s.decision for s in robot[:5]] == ["ALLOW"] * 5
    assert robot[5].decision == "DENY" and robot[5].failed == ["RUNAWAY"] and robot[5].result["actions"] == ["PAUSE"]
    assert robot[5].paused
    for s in robot[6:]:
        assert s.decision == "DENY" and s.failed == ["PAUSED"]
    gaps = {round(b.ts - a.ts, 3) for a, b in zip(robot, robot[1:])}
    assert gaps == {0.25}
    assert sum(s.result["broker"] is not None for s in robot) == 5


def test_only_8_of_50_attempts_reached_the_broker(demo):
    assert demo.attempts == 50 and demo.reached_broker == 8
    assert [o["order"]["ticker"] for o in demo.broker.orders] == ["SU26248RMFS4", "TMOS", "YDEX"] + ["SU26238RMFS4"] * 5
    assert all(o["status"] == "filled" for o in demo.broker.orders)


def test_broker_state_after_the_demo(demo):
    snap = demo.broker.snapshot()
    assert snap["positions"]["SU26248RMFS4"] == 134 and snap["positions"]["TMOS"] == 19000 + 1418
    assert snap["positions"]["YDEX"] == 1 and snap["positions"]["SU26238RMFS4"] == 210
    assert "SiZ6" not in snap["positions"] and snap["positions"]["SBER"] == 400
    expected_turnover = 4 * 905.6 + 1418 * 7.05 + 4121 + 5 * 2 * 612.5
    assert demo.gateway.state.turnover_today == pytest.approx(expected_turnover, abs=0.05)


def test_pending_orders_stay_pending(demo):
    assert [p["order"]["ticker"] for p in demo.gateway.pending_orders()] == ["SU26243RMFS4"]


def test_journal_is_intact_and_every_decision_replays(demo):
    journal = demo.gateway.journal
    assert journal.verify() is None
    assert all(journal.replay(r["seq"])["match"] for r in journal.records if r["type"] in ("decision", "confirm"))
    assert [r["type"] for r in journal.records].count("resume") == 1


def test_report_ends_with_journal_verdict_and_broker_count(demo):
    text = "\n".join(demo.lines)
    assert "Журнал: 54 записи, цепочка хешей цела." in text
    assert text.rstrip().endswith("До брокера дошло 8 из 50 попыток.")
    assert "РАСХОЖДЕНИЕ" not in text
    assert_no_long_dashes(text)


def test_demo_cli_runs(capsys):
    from mandat.demo import main

    assert main(["--no-color"]) == 0
    out = capsys.readouterr().out
    assert "S5. Зацикленный робот" in out and "До брокера дошло 8 из 50 попыток." in out


def test_color_is_optional_and_verbose_prints_every_robot_order():
    plain, colored, verbose = [], [], []
    run_demo(out=plain.append)
    run_demo(out=colored.append, color=True)
    run_demo(out=verbose.append, verbose=True)
    assert not any("\x1b[" in line for line in plain)
    assert any("\x1b[32m" in line for line in colored) and any("\x1b[31m" in line for line in colored)
    robot_lines = lambda lines: sum("buy SU26238RMFS4 x2 @ 612.5" in line for line in lines)  # noqa: E731
    assert robot_lines(plain) == 7 and robot_lines(verbose) == 40
    assert any("ещё 33 заявки" in line for line in plain) and not any("ещё 33" in line for line in verbose)
