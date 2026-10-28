"""Эталонная демонстрация (SPEC 7): десять сценариев на mock-брокере.

Запуск: python -m mandat.demo [--no-color] [--verbose] [--journal путь.jsonl]
"""

from __future__ import annotations

import argparse
import copy
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import rules, standing
from .brokers import MockBroker
from .clock import SimClock, fmt_time, parse_iso
from .explain import fmt_rub, plural
from .fixtures import load_mandate, load_market
from .gateway import Gateway
from .journal import Journal
from .lint import apply_fixes, lint

SEQUENCE = ("S1", "S2", "S3", "S6", "S7", "S8", "S10", "S9", "S4", "S5")
TITLES = {
    "S1": "Купон 4 200 ₽, реинвест",
    "S2": "10-е число, докупка по расписанию",
    "S3": "Агент хочет Яндекс (вне списка клиента)",
    "S6": "Тонкий стакан",
    "S7": "Ошибка лота",
    "S8": "Бумага в юанях",
    "S10": "Заявка сверх доли в одной бумаге",
    "S9": "Агент меняет мандат",
    "S4": "Фейковая новость: «продайте всё и купите фьючерс с плечом»",
    "S5": "Зацикленный робот: 40 одинаковых заявок, 4 в секунду",
}
SCENARIO_GAP = 600.0  # каждый следующий сценарий на 10 минут позже
ROBOT_STEP = 0.25
ROBOT_ORDERS = 40
BADGES = {"ALLOW": "РАЗРЕШЕНО", "ASK": "СПРОСИТЬ", "DENY": "ОТКАЗ"}
COLORS = {"ALLOW": "32", "ASK": "33", "DENY": "31"}


@dataclass
class Step:
    scenario: str
    ts: float
    kind: str  # order | confirm | mandate | resume
    action: str
    decision: str
    failed: list[str]  # коды проваленных проверок в порядке движка
    explanation: str
    paused: bool
    result: dict[str, Any] = field(repr=False, default_factory=dict)


@dataclass
class DemoResult:
    steps: list[Step]
    gateway: Gateway
    broker: MockBroker
    attempts: int  # попытки агента отправить заявку
    reached_broker: int  # сколько заявок дошло до брокера
    verify: int | None


def describe(order: dict[str, Any]) -> str:
    price = "рынок" if order.get("price") is None else f"{order['price']:g}"
    return f"{order['side']} {order['ticker']} x{order['quantity']} @ {price}"


class Demo:
    def __init__(
        self,
        out: Callable[[str], None] = print,
        color: bool = False,
        verbose: bool = False,
        journal=None,
        evaluate=None,
    ):
        self.out, self.color, self.verbose = out, color, verbose
        market = load_market()
        self.broker = MockBroker(market)
        self.clock = SimClock(parse_iso(market["as_of"]))
        self.t0 = self.clock.now()
        self.findings = lint(load_mandate("t1_bonds"))
        self.mandate = apply_fixes(load_mandate("t1_bonds"), self.findings)
        self.gw = Gateway(
            self.mandate, self.broker, clock=self.clock, journal=journal, evaluate=evaluate or rules.evaluate
        )
        self.steps: list[Step] = []
        self.attempts = 0
        self._scenario = ""
        self._hidden = 0
        self._paused = False

    # --- вывод ---

    def _time(self, ts: float | None = None) -> str:
        return fmt_time(self.clock.now() if ts is None else ts, ms=True).ljust(12)

    def _paint(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def _say(self, text: str = "") -> None:
        self.out(text.rstrip())

    def _heading(self, sid: str) -> None:
        self._flush_hidden()
        self._say()
        self._say(self._paint(f"{self._time()}  {sid}. {TITLES[sid]}", "1"))

    def _flush_hidden(self) -> None:
        if self._hidden:
            self._say(f"{'':14}... ещё {plural(self._hidden, 'заявка', 'заявки', 'заявок')}: ОТКАЗ (PAUSED)")
            self._hidden = 0

    def _line(self, step: Step, show: bool = True, just_paused: bool = False) -> None:
        if not show:
            self._hidden += 1
            return
        badge = self._paint(f"[{BADGES.get(step.decision, step.decision)}]".ljust(13), COLORS.get(step.decision, "0"))
        failed = f" ({', '.join(step.failed)})" if step.failed and step.decision != "ALLOW" else ""
        self._say(f"{self._time(step.ts)}  {step.action:<38} {badge}{failed}")
        self._say(f"{'':14}{step.explanation}")
        if just_paused:
            cause = "зацикливание" if "PAUSE" in step.result.get("actions", []) else "три отказа за 10 минут"
            self._say(f"{'':14}>>> {cause}: агент поставлен на паузу, снять её может только клиент")

    # --- шаги ---

    def _record(self, kind: str, action: str, result: dict[str, Any], show: bool = True) -> Step:
        failed = [c["code"] for c in result["checks"] if not c["ok"]]
        step = Step(
            self._scenario, self.clock.now(), kind, action, result["decision"], failed, result["explanation"],
            result["paused"], result,
        )  # fmt: skip
        self.steps.append(step)
        just_paused = step.paused and not self._paused
        self._paused = step.paused
        self._line(step, show, just_paused)
        return step

    def _post(self, show: bool = True, dt: float = 1.0, **order: Any) -> dict[str, Any]:
        self.clock.advance(dt)
        self.attempts += 1
        result = self.gw.post_order(**order)
        self._record("order", describe({"price": None, **order}), result, show)
        return result

    def _begin(self, index: int, sid: str) -> None:
        self._scenario = sid
        self.clock.set(self.t0 + SCENARIO_GAP * index)
        self._heading(sid)

    # --- сценарии ---

    def s1(self) -> None:
        self.clock.advance(1)
        done = standing.on_coupon(self.gw, 4200)[0]
        self.attempts += 1
        self._record("order", "купон 4 200 ₽ -> " + describe(done["order"]), done["result"])

    def s2(self) -> None:
        self.clock.advance(1)
        done = standing.on_monthly(self.gw)[0]
        self.attempts += 1
        self._record("order", "10-е число -> " + describe(done["order"]), done["result"])

    def s3(self) -> None:
        result = self._post(ticker="YDEX", side="buy", quantity=1, price=4121.0, reason="Кажется, Яндекс растёт")
        confirmed = self.gw.confirm(result["pending_id"])
        self._record("confirm", "клиент подтверждает", confirmed)

    def s6(self) -> None:
        self._post(ticker="SU26243RMFS4", side="buy", quantity=40, price=746.4)

    def s7(self) -> None:
        self._post(ticker="SBER", side="buy", quantity=15, price=305.5)

    def s8(self) -> None:
        self._post(ticker="SU52002RMFS1", side="buy", quantity=1, price=984.5)

    def s10(self) -> None:
        self._post(ticker="SBER", side="buy", quantity=130, price=305.5)

    def s9(self) -> None:
        self.clock.advance(1)
        result = self.gw.update_mandate({"allow_sell": True}, reason="Так будет удобнее")
        self._record("mandate", "update_mandate(allow_sell=true)", result)

    def s4(self) -> None:
        self._post(ticker="TMOS", side="sell", quantity=19000, reason="Новость: продайте всё")
        self._post(ticker="SBER", side="sell", quantity=400, reason="Новость: продайте всё")
        self._post(ticker="SiZ6", side="buy", quantity=10, price=84510.0, reason="Новость: купите фьючерс")

    def s5(self) -> None:
        result = self.gw.resume()
        self._say(f"{self._time()}  клиент снимает паузу")
        self._paused = False
        assert result["paused"] is False
        self.clock.advance(1 - ROBOT_STEP)  # первая заявка робота через секунду после старта
        for i in range(ROBOT_ORDERS):
            show = self.verbose or i < 7
            self._post(show=show, dt=ROBOT_STEP, ticker="SU26238RMFS4", side="buy", quantity=2, price=612.5)

    # --- прогон ---

    def run(self) -> DemoResult:
        self._say(self._paint("Мандат: демонстрация шлюза между ИИ-агентом и брокером", "1"))
        self._say(f"Мандат клиента: {self.mandate['title']}")
        self._say(f"Слова клиента: «{self.mandate['client_text']}»")
        for f in self.findings:
            fix = f" Исправление: {f['fix']['text']}." if f["fix"] else ""
            self._say(f"Стресс-тест: {f['code']} ({f['level']}) - {f['text']}{fix}")
        self._say(f"Подпись мандата: {self.gw.mandate_hash[:16]}...  Портфель: {fmt_rub(1_000_000)}")
        steps = {"S1": self.s1, "S2": self.s2, "S3": self.s3, "S6": self.s6, "S7": self.s7, "S8": self.s8}
        steps |= {"S10": self.s10, "S9": self.s9, "S4": self.s4, "S5": self.s5}
        for index, sid in enumerate(SEQUENCE):
            self._begin(index, sid)
            steps[sid]()
        self._flush_hidden()
        return DemoResult(
            self.steps, self.gw, self.broker, self.attempts, len(self.broker.orders), self.gw.journal.verify()
        )

    def report(self, result: DemoResult) -> None:
        journal = self.gw.journal
        self._say()
        self._say(self._paint("Итог", "1"))
        if result.verify is None:
            self._say(f"Журнал: {plural(len(journal), 'запись', 'записи', 'записей')}, цепочка хешей цела.")
        else:
            self._say(f"Журнал: цепочка нарушена в записи #{result.verify}.")
        forged = Journal()
        forged.records = copy.deepcopy(journal.records)
        target = next(r["seq"] for r in forged.records if r["type"] == "decision" and r["payload"].get("order"))
        forged.records[target - 1]["payload"]["order"]["quantity"] = 1
        self._say(
            f"Проверка подделки: правка суммы в записи #{target} -> verify() указывает на запись #{forged.verify()}."
        )
        denied = next(s for s in self.steps if s.scenario == "S10")
        replay = journal.replay(denied.result["seq"])
        verdict = "решение и коды проверок совпали" if replay["match"] else "РАСХОЖДЕНИЕ"
        self._say(f"Повтор решения #{replay['seq']} (S10) на сохранённых данных: {verdict}.")
        self._say(f"До брокера дошло {result.reached_broker} из {result.attempts} попыток.")


def run_demo(
    out: Callable[[str], None] = print, color: bool = False, verbose: bool = False, journal=None, evaluate=None
) -> DemoResult:
    demo = Demo(out=out, color=color, verbose=verbose, journal=journal, evaluate=evaluate)
    result = demo.run()
    demo.report(result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mandat.demo", description=__doc__)
    parser.add_argument("--no-color", action="store_true", help="без цветов")
    parser.add_argument("--verbose", action="store_true", help="печатать все 40 заявок робота")
    parser.add_argument("--journal", help="сохранять журнал в файл JSONL")
    args = parser.parse_args(argv)
    color = sys.stdout.isatty() and not args.no_color and "NO_COLOR" not in os.environ
    result = run_demo(color=color, verbose=args.verbose, journal=Journal(args.journal) if args.journal else None)
    return 0 if result.verify is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
