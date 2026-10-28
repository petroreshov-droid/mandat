import copy

import pytest

from mandat.canonical import sha256_hex
from mandat.demo import run_demo
from mandat.journal import GENESIS, Journal


@pytest.fixture(scope="module")
def demo():
    return run_demo(out=lambda _: None)


def fresh_journal(demo):
    j = Journal()
    j.records = copy.deepcopy(demo.gateway.journal.records)
    return j


def test_chain_structure(demo):
    records = demo.gateway.journal.records
    assert [r["seq"] for r in records] == list(range(1, len(records) + 1))
    assert records[0]["prev_hash"] == GENESIS == "0" * 64
    for prev, rec in zip(records, records[1:]):
        assert rec["prev_hash"] == prev["hash"]
    assert set(records[0]) == {"seq", "ts", "type", "payload", "prev_hash", "hash"}


def test_hash_formula_is_spec_4(demo):
    for rec in demo.gateway.journal.records:
        body = {k: rec[k] for k in ("seq", "ts", "type", "payload", "prev_hash")}
        assert rec["hash"] == sha256_hex(body)


def test_record_types_cover_the_demo(demo):
    types = {r["type"] for r in demo.gateway.journal.records}
    assert types == {"mandate_signed", "decision", "confirm", "resume"}


def test_decision_payload_has_the_spec_fields(demo):
    rec = next(r for r in demo.gateway.journal.records if r["type"] == "decision" and r["payload"]["order"])
    p = rec["payload"]
    assert {
        "order",
        "mandate_hash",
        "snapshot",
        "state_before",
        "checks",
        "decision",
        "explanation",
        "broker",
        "agent",
    } <= set(p)
    assert {"cash_rub", "total_value", "position_value", "instrument", "book"} <= set(p["snapshot"])
    assert {"turnover_today", "orders_last_minute", "paused", "kill_switch"} <= set(p["state_before"])


def test_verify_passes_on_untouched_journal(demo):
    assert demo.gateway.journal.verify() is None
    assert Journal().verify() is None


def test_tampering_with_any_payload_is_detected_at_that_record(demo):
    journal = fresh_journal(demo)
    for seq in range(1, len(journal) + 1):
        saved = copy.deepcopy(journal.records[seq - 1])
        journal.records[seq - 1]["payload"]["tampered"] = True
        assert journal.verify() == seq
        journal.records[seq - 1] = saved
    assert journal.verify() is None


@pytest.mark.parametrize("field", ["ts", "type", "prev_hash", "hash", "seq"])
def test_tampering_with_record_fields_is_detected(demo, field):
    journal = fresh_journal(demo)
    target = 5
    value = journal.records[target - 1][field]
    journal.records[target - 1][field] = (value + 1) if isinstance(value, (int, float)) else str(value)[::-1] + "x"
    assert journal.verify() == target


def test_editing_an_amount_and_recomputing_that_hash_breaks_the_next_link(demo):
    journal = fresh_journal(demo)
    rec = journal.records[2]
    rec["payload"]["order"]["quantity"] = 1
    rec["hash"] = sha256_hex({k: rec[k] for k in ("seq", "ts", "type", "payload", "prev_hash")})
    assert journal.verify() == rec["seq"] + 1


def test_deleted_or_reordered_records_are_detected(demo):
    journal = fresh_journal(demo)
    del journal.records[3]
    assert journal.verify() == 4
    journal = fresh_journal(demo)
    journal.records[2], journal.records[3] = journal.records[3], journal.records[2]
    assert journal.verify() == 3


def test_unserializable_garbage_in_payload_counts_as_tampering(demo):
    journal = fresh_journal(demo)
    journal.records[4]["payload"]["x"] = object()
    assert journal.verify() == 5


def test_replay_reproduces_every_decision_and_confirm(demo):
    journal = demo.gateway.journal
    replayable = [r["seq"] for r in journal.records if r["type"] in ("decision", "confirm")]
    assert len(replayable) == 52  # 50 заявок, смена мандата, подтверждение
    for seq in replayable:
        result = journal.replay(seq)
        assert result["match"], (seq, result)


def test_replay_runs_the_engine_for_orders_and_uses_fixed_rule_for_mandate_change(demo):
    journal = demo.gateway.journal
    order_rec = next(r for r in journal.records if r["type"] == "decision" and r["payload"]["order"])
    change_rec = next(r for r in journal.records if r["payload"].get("kind") == "mandate_change")
    assert journal.replay(order_rec["seq"])["engine"] is True
    fixed = journal.replay(change_rec["seq"])
    assert fixed["engine"] is False and fixed["match"] is True
    assert fixed["stored"]["checks"] == [("MANDATE_CHANGE", False, "hard")]


def test_replay_detects_a_stored_decision_that_the_engine_does_not_reproduce(demo):
    journal = fresh_journal(demo)
    rec = next(r for r in journal.records if r["type"] == "decision" and r["payload"]["decision"] == "DENY")
    rec["payload"]["decision"] = "ALLOW"
    result = journal.replay(rec["seq"])
    assert result["match"] is False
    assert result["stored"]["decision"] == "ALLOW" and result["replayed"]["decision"] == "DENY"


def test_replay_detects_changed_snapshot(demo):
    journal = fresh_journal(demo)
    rec = next(
        r
        for r in journal.records
        if r["type"] == "decision" and r["payload"]["decision"] == "ALLOW" and r["payload"]["order"]
    )
    rec["payload"]["snapshot"]["cash_rub"] = 1.0  # денег нет, FUNDS должен провалиться
    assert journal.replay(rec["seq"])["match"] is False


def test_replay_rejects_records_without_decision(demo):
    journal = demo.gateway.journal
    with pytest.raises(ValueError):
        journal.replay(1)  # mandate_signed
    with pytest.raises(KeyError):
        journal.replay(10_000)


def test_mandate_by_hash_returns_the_signed_text(demo):
    gw = demo.gateway
    assert gw.journal.mandate_by_hash(gw.mandate_hash) == gw.mandate
    assert gw.journal.mandate_by_hash("0" * 64) is None


def test_journal_file_roundtrip(demo, tmp_path):
    path = tmp_path / "journal.jsonl"
    first = Journal(path)
    for rec in demo.gateway.journal.records[:6]:
        first.append(rec["ts"], rec["type"], rec["payload"])
    assert first.records == demo.gateway.journal.records[:6]
    reloaded = Journal(path)
    assert reloaded.records == first.records and reloaded.verify() is None
    reloaded.append(1.0, "kill", {"on": True})
    assert len(Journal(path)) == 7 and Journal(path).verify() is None


def test_unknown_record_type_is_refused():
    with pytest.raises(ValueError):
        Journal().append(0.0, "oops", {})


def test_append_copies_the_payload():
    journal, payload = Journal(), {"on": True}
    journal.append(0.0, "kill", payload)
    payload["on"] = False
    assert journal.verify() is None and journal.records[0]["payload"] == {"on": True}
