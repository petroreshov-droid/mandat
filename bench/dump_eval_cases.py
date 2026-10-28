"""Записывает вызовы evaluate() в bench/eval_cases.jsonl для сверки с JS-движком.

В файл попадают: каждый вызов evaluate() эталонной демонстрации (SPEC 7) и скриптовых сценариев
(и через шлюз, и без него), а также синтетические граничные случаи: на мандатах разного устройства каждая из
19 проверок хотя бы раз проваливается и хотя бы раз проходит.

Строка файла: {"source", "input": {order, mandate, market, state},
"output": {decision, checks, explanation, order_value, actions}}. Её читает bench/cross_engine.mjs.
Запуск: python -m bench.dump_eval_cases [--out путь]
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bench.harness import load_scenarios, run_scenario
from mandat import rules
from mandat.canonical import sha256_hex
from mandat.demo import run_demo
from mandat.fixtures import demo_mandate, demo_start, load_mandate, load_market
from mandat.models import fresh_state

DEFAULT_OUT = Path(__file__).with_name("eval_cases.jsonl")
OUTPUT_KEYS = ("decision", "checks", "explanation", "order_value", "actions")


class Recorder:
    """Подменяет evaluate: копирует входы (шлюз потом меняет своё состояние), считает выход, схлопывает повторы."""

    def __init__(self) -> None:
        self.cases: dict[str, dict[str, Any]] = {}
        self.calls = 0
        self.source = ""

    def __call__(self, order: Any, mandate: Any, market: dict, state: dict) -> dict[str, Any]:
        self.calls += 1
        case_input = copy.deepcopy({"order": order, "mandate": mandate, "market": market, "state": state})
        output = rules.evaluate(order, mandate, market, state)
        self.cases.setdefault(
            sha256_hex(case_input),
            {"source": self.source, "input": case_input, "output": {k: output[k] for k in OUTPUT_KEYS}},
        )
        return output


# --- синтетические граничные случаи ---

EDGE_ORDERS = [  # (тикер, сторона, количество, цена)
    ("TMOS", "buy", 1, None),
    ("TMOS", "buy", 2273, 7.06),
    ("TMOS", "buy", 0, None),
    ("TMOS", "buy", -5, None),
    ("TMOS", "buy", 2.5, None),
    ("TMOS", "sell", 19000, None),
    ("SBER", "buy", 10, 305.5),
    ("SBER", "buy", 15, 305.5),
    ("SBER", "buy", 130, 305.5),
    ("SBER", "buy", 10, 310.0),
    ("SBER", "sell", 400, None),
    ("SBER", "sell", 410, None),
    ("LKOH", "buy", 8, 6812.0),
    ("LKOH", "buy", 40, 6812.0),
    ("YDEX", "buy", 1, 4121.0),
    ("YDEX", "buy", 10, 4121.0),
    ("SU26243RMFS4", "buy", 12, 746.4),
    ("SU26243RMFS4", "buy", 40, 746.4),
    ("SU26243RMFS4", "sell", 15, None),
    ("SU52002RMFS1", "buy", 1, 984.5),
    ("SiZ6", "buy", 10, 84510.0),
    ("SiZ6", "sell", 1, None),
    ("STRUCT1", "buy", 5, 1000.0),
    ("NOPE", "buy", 1, None),
]


def edge_mandates() -> dict[str, dict[str, Any]]:
    t1 = demo_mandate()
    margin = {
        **t1,
        "allow_sell": True,
        "forbid_leverage": False,
        "forbid_qual_only": False,
        "allowed_currencies": ["rub", "cny"],
        "allowed_classes": ["bond", "etf", "share", "futures"],
    }
    bare = copy.deepcopy(t1)
    bare["limits"] = dict.fromkeys(bare["limits"])
    return {
        "t1": t1,
        "t2": load_mandate("t2_free"),
        "t1-strict": {**t1, "strict_whitelist": True},
        "t1-sell": {**t1, "allow_sell": True},
        "t1-margin": margin,
        "t1-no-limits": bare,
    }


def _order(ticker: str, side: str, quantity: float, price: float | None, order_id: str | None = None) -> dict:
    return {
        "order_id": order_id,
        "ticker": ticker,
        "side": side,
        "quantity": quantity,
        "price": price,
        "agent": "edge",
        "reason": "",
    }


def record_edge_cases(rec: Recorder) -> None:
    market, now = load_market(), demo_start()
    mandates = edge_mandates()
    for name, mandate in mandates.items():
        for i, (ticker, side, qty, price) in enumerate(EDGE_ORDERS):
            rec.source = f"edge:{name}:{i}"
            rec(_order(ticker, side, qty, price), mandate, copy.deepcopy(market), fresh_state(now=now))

    t1, sber = mandates["t1"], _order("SBER", "buy", 10, 305.5)
    states = {  # состояние шлюза: выключатель, пауза, повтор, частота (в том числе граница ровно 60 с), дневной лимит
        "kill": {"kill_switch": True},
        "paused": {"paused": True},
        "paused-and-kill": {"paused": True, "kill_switch": True},
        "runaway-5": {"recent_order_ts": [now - 1] * 5},
        "runaway-4": {"recent_order_ts": [now - 1] * 4},
        "runaway-edge-60": {"recent_order_ts": [now - 60] * 5},
        "runaway-edge-59": {"recent_order_ts": [now - 59.75] * 5},
        "turnover-over": {"turnover_today": 99000},
        "turnover-edge": {"turnover_today": 96945},
    }
    for name, patch in states.items():
        rec.source = f"edge:state:{name}"
        rec(sber, t1, copy.deepcopy(market), fresh_state(now=now, **patch))
    rec.source = "edge:state:duplicate"
    rec(
        _order("SBER", "buy", 10, 305.5, "dup-1"),
        t1,
        copy.deepcopy(market),
        fresh_state(now=now, seen_order_ids=["dup-1"]),
    )
    rec.source = "edge:state:new-id"
    rec(
        _order("SBER", "buy", 10, 305.5, "new-1"),
        t1,
        copy.deepcopy(market),
        fresh_state(now=now, seen_order_ids=["dup-1"]),
    )

    def with_market(name: str, order: dict, mutate) -> None:
        patched = copy.deepcopy(market)
        mutate(patched)
        rec.source = f"edge:market:{name}"
        rec(order, t1, patched, fresh_state(now=now))

    with_market("halted", sber, lambda m: m["instruments"]["SBER"].update(status="halted"))
    with_market("closed", sber, lambda m: m["instruments"]["SBER"].update(status="closed"))
    with_market("no-book", sber, lambda m: m["books"].pop("SBER"))
    with_market("empty-bids-book", sber, lambda m: m["books"]["SBER"].update(bids=[]))
    with_market("empty-asks-book", sber, lambda m: m["books"]["SBER"].update(asks=[]))
    with_market("thin-book", _order("SBER", "buy", 50, 305.5), lambda m: m["books"]["SBER"].update(asks=[[305.5, 20]]))
    with_market("cash-equals-value", sber, lambda m: m.update(cash_rub=3055.0))
    with_market("cash-one-kopeck-short", sber, lambda m: m.update(cash_rub=3054.99))
    with_market("no-cash", sber, lambda m: m.update(cash_rub=0.0))
    with_market("no-positions", sber, lambda m: m.update(positions={}))
    with_market("price-edge-1pct", _order("SBER", "buy", 10, 308.454), lambda m: None)
    with_market("fx-moved", _order("SU52002RMFS1", "buy", 1, 984.5), lambda m: m["fx"].update(cny=12.5))
    margin = mandates["t1-margin"]
    rec.source = "edge:market:margin-cny"
    rec(_order("SU52002RMFS1", "buy", 2, 984.5), margin, copy.deepcopy(market), fresh_state(now=now))


def collect() -> Recorder:
    rec = Recorder()
    rec.source = "demo"
    run_demo(out=lambda _: None, evaluate=rec)
    for scenario in load_scenarios():
        rec.source = f"{scenario['id']}:mandat"
        run_scenario(scenario, "mandat", evaluate=rec, probe_evaluate=rec)
        rec.source = f"{scenario['id']}:direct"
        run_scenario(scenario, "direct", probe_evaluate=rec)
    record_edge_cases(rec)
    return rec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.dump_eval_cases", description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    rec = collect()
    with args.out.open("w", encoding="utf-8") as fh:
        for case in rec.cases.values():
            fh.write(json.dumps(case, ensure_ascii=False, separators=(",", ":")) + "\n")
    decisions: dict[str, int] = {}
    for case in rec.cases.values():
        decisions[case["output"]["decision"]] = decisions.get(case["output"]["decision"], 0) + 1
    size = args.out.stat().st_size / 1e6
    print(f"{len(rec.cases)} случаев из {rec.calls} вызовов evaluate(), {size:.1f} МБ: {decisions} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
