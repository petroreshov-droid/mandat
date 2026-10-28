"""Прогон скриптовых сценариев: через шлюз (mandat) и напрямую к брокеру (direct).

Сценарий описывает, что пытается сделать плохой или ошибающийся агент. Прямой режим показывает, что дошло бы до
брокера без шлюза. В обоих режимах брокер обёрнут в Probe: он независимо от шлюза оценивает каждую заявку,
которая до брокера дошла.
"""

from __future__ import annotations

import copy
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from mandat import rules, standing
from mandat.brokers import Broker, MockBroker
from mandat.clock import MSK, SimClock
from mandat.fixtures import demo_mandate, demo_start, load_market
from mandat.gateway import Gateway
from mandat.models import ALLOW, Order, fresh_state

SCENARIOS_PATH = Path(__file__).with_name("scenarios.jsonl")
DEFAULT_DT = 1.0
CONFIRM_DT = 0.0
PROBE_AGENT = "scripted"

Evaluator = Callable[..., dict[str, Any]]


def load_scenarios(path: Path = SCENARIOS_PATH) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


class Probe(Broker):
    """Обёртка над брокером: перед исполнением независимо оценивает заявку движком и ведёт свой учёт.

    Состояние Probe не зависит от шлюза: только то, что реально ушло брокеру (оборот, номера, время попыток).
    """

    def __init__(
        self,
        inner: MockBroker,
        mandate: dict[str, Any],
        clock: SimClock,
        *,
        turnover: float = 0.0,
        recent: list[float] | None = None,
        seen: list[str] | None = None,
        kill_switch: bool = False,
        paused: bool = False,
        evaluate: Evaluator = rules.evaluate,
    ):
        self.inner, self.mandate, self.clock, self._evaluate = inner, mandate, clock, evaluate
        self.turnover = turnover
        self._recent, self._seen = list(recent or []), list(seen or [])
        self._flags = {"kill_switch": kill_switch, "paused": paused}  # решения клиента: без шлюза их некому исполнять
        self.confirming = False  # True, пока клиент подтверждает заявку: она уходит с его согласия
        self.sent: list[dict[str, Any]] = []

    def snapshot(self) -> dict[str, Any]:
        return self.inner.snapshot()

    def credit_cash(self, amount: float) -> None:
        self.inner.credit_cash(amount)

    def place_order(self, order: dict[str, Any]) -> dict[str, Any]:
        now = self.clock.now()
        state = fresh_state(
            now=now,
            turnover_today=self.turnover,
            recent_order_ts=self._recent,
            seen_order_ids=self._seen,
            **self._flags,
        )
        verdict = self._evaluate(order, self.mandate, self.inner.snapshot(), state)
        response = self.inner.place_order(order)
        self._recent.append(now)
        if order.get("order_id") is not None:
            self._seen.append(order["order_id"])
        filled = response["status"] == "filled"
        if filled:
            self.turnover += verdict["order_value"]
        self.sent.append(
            {
                "order": order,
                "decision": verdict["decision"],
                "failed": [c["code"] for c in verdict["checks"] if not c["ok"]],
                "filled": filled,
                "confirmed": self.confirming,
                "response": response,
            }
        )
        return response


class DirectGateway:
    """Агент без шлюза: каждая заявка уходит брокеру. Решение в ответе - оценка Probe, не ограничение."""

    def __init__(self, mandate: dict[str, Any], probe: Probe, clock: SimClock):
        self.mandate, self.broker, self.clock = mandate, probe, clock

    def today(self) -> date:
        return datetime.fromtimestamp(self.clock.now(), MSK).date()

    def post_order(self, **raw: Any) -> dict[str, Any]:
        order = Order.parse(raw).to_dict()
        response = self.broker.place_order(order)
        sent = self.broker.sent[-1]
        return {
            "decision": sent["decision"],
            "checks": [{"code": c, "ok": False} for c in sent["failed"]],
            "broker": {"order_id": response["order_id"], "status": response["status"]},
            "pending_id": None,
        }


@dataclass
class StepResult:
    index: int
    tool: str
    expect: str | None
    decision: str | None  # ALLOW | ASK | DENY | NONE (поручение не создало заявку) | None (шаг без решения)
    failed: list[str] = field(default_factory=list)
    orders: int = 0  # сколько заявок агент попытался отправить на этом шаге
    sent: int = 0  # сколько из них дошло до брокера


@dataclass
class ScenarioRun:
    scenario: dict[str, Any]
    mode: str
    steps: list[StepResult]
    sent: list[dict[str, Any]]  # то, что увидел Probe
    mandate_edits: int = 0
    edits_accepted: int = 0


def _initial_state(setup: dict[str, Any], start: float) -> dict[str, Any]:
    state = dict(setup.get("state", {}))
    state["recent_order_ts"] = [start + dt for dt in state.get("recent_order_ts", [])]
    return state


def run_scenario(
    scenario: dict[str, Any],
    mode: str,
    *,
    evaluate: Evaluator = rules.evaluate,
    probe_evaluate: Evaluator = rules.evaluate,
    timings: dict[str, list[tuple[int, Any]]] | None = None,
) -> ScenarioRun:
    """mode: "mandat" (через Gateway) или "direct".

    timings: сюда пишутся замеры в наносекундах: evaluate - пары (нс, число проверок), post_order - пары (нс, решение).
    """
    setup = scenario.get("setup") or {}
    market = deep_merge(load_market(), setup.get("market", {}))
    mandate = demo_mandate()
    start = demo_start()
    clock = SimClock(start)
    initial = _initial_state(setup, start)
    probe = Probe(
        MockBroker(market),
        mandate,
        clock,
        turnover=initial.get("turnover_today", 0.0),
        recent=initial["recent_order_ts"],
        seen=initial.get("seen_order_ids"),
        kill_switch=initial.get("kill_switch", False),
        paused=initial.get("paused", False),
        evaluate=probe_evaluate,
    )
    if mode == "mandat":
        if timings is not None:
            evaluate = _timed(evaluate, timings["evaluate"])
        gw: Any = Gateway(mandate, probe, clock=clock, evaluate=evaluate)
        for key in ("turnover_today", "paused", "kill_switch"):
            if key in initial:
                setattr(gw.state, key, initial[key])
        gw.state.recent_order_ts.extend(initial["recent_order_ts"])
        gw.state.seen_order_ids.extend(initial.get("seen_order_ids", []))
    else:
        gw = DirectGateway(mandate, probe, clock)

    run = ScenarioRun(scenario, mode, [], probe.sent)
    pending: dict[int, str] = {}
    last_pending: str | None = None
    for index, step in enumerate(scenario["steps"]):
        tool = step["tool"]
        clock.advance(step.get("dt", CONFIRM_DT if tool == "confirm" else DEFAULT_DT))
        sent_before, args = len(probe.sent), step.get("args", {})
        result = StepResult(index, tool, step.get("expect"), None)
        results: list[dict[str, Any]] = []
        if tool == "post_order":
            call = {"agent": PROBE_AGENT, **args}
            started = time.perf_counter_ns()
            results = [gw.post_order(**call)]
            if timings is not None and mode == "mandat":
                timings["post_order"].append((time.perf_counter_ns() - started, results[0]["decision"]))
        elif tool == "coupon":
            results = _decisions(standing.on_coupon(gw, args["amount"], event_id=args.get("event_id")))
        elif tool == "monthly":
            results = _decisions(standing.on_monthly(gw, day=args.get("day")))
        elif tool == "update_mandate":
            run.mandate_edits += 1
            if mode == "mandat":
                results = [gw.update_mandate(args.get("changes"), agent=PROBE_AGENT)]
            else:
                run.edits_accepted += 1  # без шлюза менять некому мешать
        elif tool == "resume":
            if mode == "mandat":
                gw.resume()
        elif tool == "confirm":
            if mode == "mandat":
                target = pending.get(args["step"]) if "step" in args else last_pending
                if target is None:
                    results = [{"decision": "NO_PENDING", "checks": []}]
                else:
                    probe.confirming = True
                    try:
                        results = [gw.confirm(target)]
                    finally:
                        probe.confirming = False
        else:
            raise ValueError(f"Неизвестный инструмент шага: {tool}")

        if tool in ("post_order", "coupon", "monthly"):
            result.orders = len(results)
        if results:
            first = results[0]
            result.decision = first["decision"]
            result.failed = [c["code"] for c in first["checks"] if not c["ok"]]
            if first.get("pending_id") and tool != "confirm":
                pending[index] = last_pending = first["pending_id"]
        elif tool in ("coupon", "monthly"):
            result.decision = "NONE"
        result.sent = len(probe.sent) - sent_before
        run.steps.append(result)
    return run


def _decisions(done: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [d["result"] for d in done if d["result"] is not None]


def _timed(evaluate: Evaluator, bucket: list[tuple[int, Any]]) -> Evaluator:
    def wrapper(*args: Any) -> dict[str, Any]:
        started = time.perf_counter_ns()
        result = evaluate(*args)
        bucket.append((time.perf_counter_ns() - started, len(result["checks"])))
        return result

    return wrapper


def is_violation(sent: dict[str, Any]) -> bool:
    """Заявка, которую движок не разрешил бы по мандату, и клиент её не подтверждал."""
    return sent["decision"] != ALLOW and not sent["confirmed"]
