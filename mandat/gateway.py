"""Шлюз (SPEC 3): единственный путь заявки от агента к брокеру.

Агентские методы: get_portfolio, get_instrument, get_orderbook, get_mandate, post_order, update_mandate.
Клиентские (агенту недоступны): sign_mandate, confirm, reject, kill_switch, resume.
"""

from __future__ import annotations

import copy
import json
import threading
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any

from . import rules
from .brokers.base import REJECTED_STATUSES, Broker
from .canonical import sha256_hex
from .clock import MSK, SystemClock
from .compiler import validate
from .explain import mandate_rules
from .journal import Journal
from .lint import has_critical, lint
from .models import ALLOW, ASK, DENY, GatewayState, Mandate, Order, OrderFormatError, Pending

STRIKES_TO_PAUSE = 3  # столько отказов в окне STRIKE_WINDOW - и агент на паузе
STRIKE_WINDOW = 600.0  # секунд (строго now - ts < 600)
CHANGE_DETAIL = "Мандат меняет только клиент через подпись новой версии."
CONFIRMED_TEXT = "Подтверждено клиентом: заявка отправлена брокеру."

Evaluator = Callable[[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]]


class GatewayError(Exception):
    """Неверное обращение к шлюзу (например, неизвестная заявка на подтверждение)."""


class MandateRejected(GatewayError):
    """Мандат не подписан: структура неверна или стресс-тест нашёл critical."""

    def __init__(self, problems: list[str], findings: list[dict[str, Any]] | None = None):
        self.problems = problems
        self.findings = findings or []
        super().__init__("Мандат не подписан: " + "; ".join(problems))


def _check(code: str, detail: str) -> dict[str, Any]:
    return {"code": code, "ok": False, "severity": "hard", "detail": detail}


class Gateway:
    def __init__(
        self,
        mandate: dict[str, Any],
        broker: Broker,
        *,
        clock: Any = None,
        journal: Journal | None = None,
        evaluate: Evaluator = rules.evaluate,
        tz: timezone = MSK,
        signed_by: str = "client",
    ):
        self.broker = broker
        self.clock = clock or SystemClock()
        self.journal = journal if journal is not None else Journal()
        self._evaluate = evaluate
        self._utc_offset = tz.utcoffset(None).total_seconds()
        self._tz = tz
        self._lock = threading.RLock()
        self.state = GatewayState(now=self.clock.now())
        self._day = self._day_of(self.state.now)
        self._pending: dict[str, Pending] = {}
        self.mandate: dict[str, Any] = {}
        self.mandate_hash = ""
        self.sign_mandate(mandate, signed_by=signed_by)

    # --- время ---

    def _day_of(self, now: float) -> int:
        return int((now + self._utc_offset) // 86400)

    def today(self) -> date:
        return datetime.fromtimestamp(self.clock.now(), self._tz).date()

    def _tick(self) -> float:
        """Берёт время с часов, обнуляет дневной оборот при смене дня, чистит устаревшие отметки."""
        now = self.clock.now()
        st = self.state
        st.now = now
        day = self._day_of(now)
        if day != self._day:
            self._day, st.turnover_today = day, 0.0
        st.recent_order_ts[:] = [ts for ts in st.recent_order_ts if now - ts < rules.WINDOW_RUNAWAY]
        st.violation_ts[:] = [ts for ts in st.violation_ts if now - ts < STRIKE_WINDOW]
        return now

    # --- клиентские действия ---

    def sign_mandate(self, mandate: dict[str, Any], signed_by: str = "client") -> dict[str, Any]:
        """Подпись новой версии мандата. Структура должна быть верной, critical в стресс-тесте - исправлены."""
        problems = validate(mandate)
        if problems:
            raise MandateRejected(problems)
        normalized = Mandate.from_dict(mandate).to_dict()
        findings = lint(normalized)
        if has_critical(findings):
            critical = [f for f in findings if f["level"] == "critical"]
            raise MandateRejected([f"{f['code']}: {f['text']}" for f in critical], critical)
        with self._lock:
            self.mandate, self.mandate_hash = normalized, sha256_hex(normalized)
            payload = {
                "mandate": normalized,
                "mandate_hash": self.mandate_hash,
                "signed_by": signed_by,
                "lint": [f["code"] for f in findings],
            }
            self.journal.append(self.clock.now(), "mandate_signed", payload)
        return {"hash": self.mandate_hash, "lint": findings}

    def kill_switch(self, on: bool) -> dict[str, Any]:
        with self._lock:
            now = self._tick()
            self.state.kill_switch = bool(on)
            rec = self.journal.append(now, "kill", {"on": bool(on)})
            return {"kill_switch": self.state.kill_switch, "seq": rec["seq"]}

    def resume(self) -> dict[str, Any]:
        """Снимает паузу и очищает отказы: считать три отказа заново."""
        with self._lock:
            now = self._tick()
            payload = {"was_paused": self.state.paused, "cleared_violations": len(self.state.violation_ts)}
            self.state.paused = False
            self.state.violation_ts.clear()
            rec = self.journal.append(now, "resume", payload)
            return {"paused": False, "seq": rec["seq"]}

    def pending_orders(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {"pending_id": p.pending_id, "order": dict(p.order), "ts": p.ts}
                for p in self._pending.values()
                if p.status == "pending"
            ]

    def _pending_or_raise(self, pending_id: str) -> Pending:
        pending = self._pending.get(pending_id)
        if pending is None:
            raise GatewayError(f"Нет заявки {pending_id} на подтверждении.")
        if pending.status != "pending":
            raise GatewayError(f"Заявка {pending_id} уже обработана: {pending.status}.")
        return pending

    def confirm(self, pending_id: str) -> dict[str, Any]:
        """Подтверждение клиента: жёсткие проверки заново на свежем снимке и состоянии, мягкие не повторяются."""
        with self._lock:
            pending = self._pending_or_raise(pending_id)
            now = self._tick()
            order = pending.order
            market, broker_down = self._snapshot(order["ticker"])
            if broker_down is not None:  # заявка остаётся ждать: клиент сможет подтвердить ещё раз
                raise GatewayError(f"Брокер недоступен, заявка {pending_id} не отправлена: {broker_down}")
            # сама отложенная заявка уже учтена в состоянии: для повторной проверки её вычитаем
            recent = list(self.state.recent_order_ts)
            if pending.ts in recent:
                recent.remove(pending.ts)
            state = {
                **self.state.as_dict(),
                "recent_order_ts": recent,
                "seen_order_ids": [i for i in self.state.seen_order_ids if i != order["order_id"]],
            }
            result = self._evaluate(order, self.mandate, market, state)
            hard = [c for c in result["checks"] if not c["ok"] and c["severity"] == "hard"]
            broker_info = None
            if hard:
                decision, explanation = DENY, f"Подтверждение не сработало: {hard[0]['detail']}"
            else:
                decision, explanation = ALLOW, CONFIRMED_TEXT
                broker_info = self._send(order, result["order_value"])
                pending.status = "confirmed"
            payload = {
                "pending_id": pending_id,
                "ref_seq": pending.seq,
                "order": order,
                "mandate_hash": self.mandate_hash,
                "snapshot": rules.build_view(order, market),
                "state_before": self._state_before(now, order, recent, state["seen_order_ids"]),
                "checks": result["checks"],
                "engine_decision": result["decision"],
                "decision": decision,
                "explanation": explanation,
                "broker": broker_info,
                "agent": order["agent"],
            }
            rec = self.journal.append(now, "confirm", payload)
            return {
                **result,
                "decision": decision,
                "explanation": explanation,
                "pending_id": pending_id,
                "broker": broker_info,
                "seq": rec["seq"],
                "paused": self.state.paused,
            }

    def reject(self, pending_id: str, reason: str = "") -> dict[str, Any]:
        with self._lock:
            pending = self._pending_or_raise(pending_id)
            now = self._tick()
            pending.status = "rejected"
            payload = {
                "pending_id": pending_id,
                "ref_seq": pending.seq,
                "order": pending.order,
                "mandate_hash": self.mandate_hash,
                "reason": reason[:500],
            }
            rec = self.journal.append(now, "reject", payload)
            return {"status": "rejected", "pending_id": pending_id, "seq": rec["seq"]}

    # --- агентские действия ---

    def post_order(
        self,
        ticker: str,
        side: str,
        quantity: int | float,
        price: float | None = None,
        order_id: str | None = None,
        agent: str = "agent",
        reason: str = "",
    ) -> dict[str, Any]:
        raw = {
            "ticker": ticker,
            "side": side,
            "quantity": quantity,
            "price": price,
            "order_id": order_id,
            "agent": agent,
            "reason": reason,
        }
        with self._lock:
            now = self._tick()
            try:
                order = Order.parse(raw).to_dict()
            except OrderFormatError as exc:
                return self._deny_malformed(raw, now, str(exc))

            before = self._state_before(now, order, self.state.recent_order_ts, self.state.seen_order_ids)
            market, broker_down = self._snapshot(order["ticker"])
            if broker_down is not None:
                self.state.recent_order_ts.append(now)  # попытка учитывается для RUNAWAY
                return self._deny_without_engine(
                    "broker_unavailable",
                    _check("BROKER_UNAVAILABLE", broker_down),
                    now,
                    before,
                    order["agent"],
                    strike=False,  # сбой брокера - не вина агента
                    order=order,
                )
            result = self._evaluate(order, self.mandate, market, self.state.as_dict())

            st = self.state
            st.recent_order_ts.append(now)
            if order["order_id"] is not None and order["order_id"] not in st.seen_order_ids:
                st.seen_order_ids.append(order["order_id"])
            if result["decision"] == DENY:
                self._strike(now)
            if "PAUSE" in result["actions"]:
                st.paused = True

            broker_info = pending_id = None
            if result["decision"] == ALLOW:
                broker_info = self._send(order, result["order_value"])
            payload = {
                "kind": "order",
                "order": order,
                "mandate_hash": self.mandate_hash,
                "snapshot": rules.build_view(order, market),
                "state_before": before,
                "checks": result["checks"],
                "decision": result["decision"],
                "explanation": result["explanation"],
                "actions": result["actions"],
                "broker": broker_info,
                "agent": order["agent"],
                "paused_after": st.paused,
            }
            if result["decision"] == ASK:
                pending_id = self._park(order, now)
                payload["pending_id"] = pending_id
            rec = self.journal.append(now, "decision", payload)
            if pending_id:
                self._pending[pending_id].seq = rec["seq"]
            return {**result, "pending_id": pending_id, "broker": broker_info, "seq": rec["seq"], "paused": st.paused}

    def update_mandate(self, changes: Any = None, agent: str = "agent", reason: str = "") -> dict[str, Any]:
        """Агент мандат менять не может: всегда отказ MANDATE_CHANGE, и это считается нарушением."""
        with self._lock:
            now = self._tick()
            before = self._state_before(now, None, self.state.recent_order_ts, self.state.seen_order_ids)
            return self._deny_without_engine(
                "mandate_change",
                _check("MANDATE_CHANGE", CHANGE_DETAIL),
                now,
                before,
                agent,
                requested=json.dumps(changes, ensure_ascii=False, default=str)[:2000],
                reason=reason[:1000],
            )

    # --- чтение для агента ---

    def get_portfolio(self) -> dict[str, Any]:
        market = self.broker.snapshot()
        total = rules.total_value(market)
        positions = []
        for ticker, qty in market["positions"].items():
            inst = market["instruments"][ticker]
            value = qty * inst["last"] * market["fx"][inst["currency"]]
            positions.append(
                {
                    "ticker": ticker,
                    "name": inst["name"],
                    "quantity": qty,
                    "value_rub": round(value, 2),
                    "share": round(value / total, 4) if total else 0.0,
                }
            )
        return {
            "as_of": market["as_of"],
            "cash_rub": market["cash_rub"],
            "total_value_rub": round(total, 2),
            "positions": positions,
        }

    def get_instrument(self, ticker: str) -> dict[str, Any]:
        inst = self.broker.snapshot()["instruments"].get(ticker)
        if inst is None:
            return {"error": f"Бумага {ticker} не найдена."}
        return {"ticker": ticker, **inst}

    def get_orderbook(self, ticker: str) -> dict[str, Any]:
        book = self.broker.snapshot().get("books", {}).get(ticker)
        if book is None:
            return {"error": f"Стакана по {ticker} нет."}
        return {"ticker": ticker, "asks": book["asks"], "bids": book["bids"]}

    def get_mandate(self) -> dict[str, Any]:
        with self._lock:
            names = {t: i["name"] for t, i in self.broker.snapshot()["instruments"].items()}
            st = self.state
            limit = self.mandate["limits"]["daily_turnover"]
            return {
                "hash": self.mandate_hash,
                "title": self.mandate["title"],
                "client_text": self.mandate["client_text"],
                "rules": mandate_rules(self.mandate, names),
                "mandate": copy.deepcopy(self.mandate),  # копия: агент не должен менять подписанный объект
                "status": {
                    "paused": st.paused,
                    "kill_switch": st.kill_switch,
                    "turnover_today_rub": st.turnover_today,
                    "daily_left_rub": None if limit is None else max(limit - st.turnover_today, 0),
                },
            }

    # --- внутреннее ---

    def _state_before(
        self, now: float, order: dict[str, Any] | None, recent: list[float], seen: list[str]
    ) -> dict[str, Any]:
        order_id = order["order_id"] if order else None
        return {
            "turnover_today": self.state.turnover_today,
            "orders_last_minute": sum(1 for ts in recent if now - ts < rules.WINDOW_RUNAWAY),
            "paused": self.state.paused,
            "kill_switch": self.state.kill_switch,
            "order_id_seen": order_id is not None and order_id in seen,
        }

    def _strike(self, now: float) -> None:
        """Отказ засчитан нарушением; три за 10 минут - пауза."""
        st = self.state
        st.violation_ts.append(now)
        if sum(1 for ts in st.violation_ts if now - ts < STRIKE_WINDOW) >= STRIKES_TO_PAUSE:
            st.paused = True

    def _snapshot(self, focus: str) -> tuple[dict[str, Any] | None, str | None]:
        """Снимок рынка у брокера. Сбой не роняет шлюз: заявка без снимка не проверяется и не отправляется."""
        try:
            return self.broker.snapshot(focus=focus), None
        except Exception as exc:  # noqa: BLE001 - любой сбой брокера означает «не проверено, не отправлено»
            return None, f"нет данных брокера для проверки ({type(exc).__name__}: {exc})"[:300]

    def _send(self, order: dict[str, Any], order_value: float) -> dict[str, Any]:
        try:
            response = self.broker.place_order(order)
        except Exception as exc:  # сбой брокера не должен ронять шлюз; заявка не считается исполненной
            return {"order_id": None, "status": "error", "reason": f"{type(exc).__name__}: {exc}"[:300]}
        info = {"order_id": response.get("order_id"), "status": response.get("status", "unknown")}
        if response.get("reason"):
            info["reason"] = response["reason"]
        if info["status"] not in REJECTED_STATUSES:
            self.state.turnover_today += order_value
        return info

    def _park(self, order: dict[str, Any], now: float) -> str:
        pending_id = f"P{len(self._pending) + 1:04d}"
        self._pending[pending_id] = Pending(pending_id, order, now, self.mandate_hash, seq=0)
        return pending_id

    def _deny_malformed(self, raw: dict[str, Any], now: float, message: str) -> dict[str, Any]:
        before = self._state_before(now, None, self.state.recent_order_ts, self.state.seen_order_ids)
        self.state.recent_order_ts.append(now)  # кривая заявка - тоже попытка
        return self._deny_without_engine(
            "malformed",
            _check("ORDER_FORMAT", message),
            now,
            before,
            str(raw.get("agent")),
            received=json.dumps(raw, ensure_ascii=False, default=str)[:2000],
        )

    def _deny_without_engine(
        self,
        kind: str,
        check: dict[str, Any],
        now: float,
        before: dict[str, Any],
        agent: str,
        *,
        strike: bool = True,
        **extra: Any,
    ) -> dict[str, Any]:
        """Отказ мимо движка (смена мандата, неверный формат, нет данных брокера): запись в журнал, обычно и нарушение."""
        if strike:
            self._strike(now)
        explanation = f"Отклонено: {check['detail']}"
        payload = {
            "kind": kind,
            "order": None,
            **extra,
            "mandate_hash": self.mandate_hash,
            "state_before": before,
            "checks": [check],
            "decision": DENY,
            "explanation": explanation,
            "actions": [],
            "broker": None,
            "agent": agent[:100],
            "paused_after": self.state.paused,
        }
        rec = self.journal.append(now, "decision", payload)
        return {
            "decision": DENY,
            "checks": [check],
            "explanation": explanation,
            "order_value": 0.0,
            "actions": [],
            "pending_id": None,
            "broker": None,
            "seq": rec["seq"],
            "paused": self.state.paused,
        }
