"""Журнал решений: цепочка хешей (SPEC 4). Любая правка прошлой записи ломает цепочку."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from .canonical import sha256_hex
from .models import DENY
from .rules import evaluate_view

GENESIS = "0" * 64
RECORD_TYPES = ("mandate_signed", "decision", "confirm", "reject", "kill", "resume")
_HASHED = ("seq", "ts", "type", "payload", "prev_hash")


def _signature(check_list: list[dict[str, Any]]) -> list[tuple[str, bool, str]]:
    return [(c["code"], c["ok"], c["severity"]) for c in check_list]


class Journal:
    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.records: list[dict[str, Any]] = []
        self._path = Path(path) if path else None
        if self._path and self._path.exists():
            with self._path.open(encoding="utf-8") as fh:
                self.records = [json.loads(line) for line in fh if line.strip()]

    def __len__(self) -> int:
        return len(self.records)

    @property
    def head(self) -> str:
        return self.records[-1]["hash"] if self.records else GENESIS

    def get(self, seq: int) -> dict[str, Any]:
        if not 1 <= seq <= len(self.records):
            raise KeyError(f"В журнале нет записи {seq}")
        return self.records[seq - 1]

    def append(self, ts: float, record_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if record_type not in RECORD_TYPES:
            raise ValueError(f"Неизвестный тип записи журнала: {record_type}")
        body = {
            "seq": len(self.records) + 1,
            "ts": ts,
            "type": record_type,
            "payload": copy.deepcopy(payload),
            "prev_hash": self.head,
        }
        record = {**body, "hash": sha256_hex(body)}
        self.records.append(record)
        if self._path:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        return record

    def verify(self) -> int | None:
        """Номер первой битой записи или None, если цепочка цела."""
        prev = GENESIS
        for position, rec in enumerate(self.records, start=1):
            try:
                good = (
                    rec.get("seq") == position
                    and rec.get("prev_hash") == prev
                    and rec.get("hash") == sha256_hex({k: rec.get(k) for k in _HASHED})
                )
            except (TypeError, ValueError):
                good = False
            if not good:
                return position
            prev = rec["hash"]
        return None

    def mandate_by_hash(self, mandate_hash: str) -> dict[str, Any] | None:
        for rec in self.records:
            payload = rec["payload"]
            signed = rec["type"] == "mandate_signed" and payload.get("mandate_hash") == mandate_hash
            if signed and sha256_hex(payload["mandate"]) == mandate_hash:
                return payload["mandate"]
        return None

    def replay(self, seq: int) -> dict[str, Any]:
        """Заново прогоняет движок на сохранённых в записи заявке, мандате, снимке и состоянии."""
        rec = self.get(seq)
        payload = rec["payload"]
        if rec["type"] not in ("decision", "confirm"):
            raise ValueError(f"Запись {seq} ({rec['type']}) не содержит решения")
        stored_decision = payload.get("engine_decision", payload["decision"])
        stored = {"decision": stored_decision, "checks": _signature(payload["checks"])}

        if payload.get("order") is None:  # решение без движка: смена мандата, неверный формат заявки
            fixed_rule = stored_decision == DENY and len(payload["checks"]) == 1 and not payload["checks"][0]["ok"]
            return {"seq": seq, "match": fixed_rule, "engine": False, "stored": stored, "replayed": stored}

        mandate = self.mandate_by_hash(payload["mandate_hash"])
        if mandate is None:
            raise LookupError(f"В журнале нет подписанного мандата {payload['mandate_hash'][:12]}")
        before, now = payload["state_before"], rec["ts"]
        order_id = payload["order"].get("order_id")
        state = {
            "now": now,
            "turnover_today": before["turnover_today"],
            "recent_order_ts": [now] * before["orders_last_minute"],
            "seen_order_ids": [order_id] if before.get("order_id_seen") else [],
            "paused": before["paused"],
            "kill_switch": before["kill_switch"],
        }
        out = evaluate_view(payload["order"], mandate, payload["snapshot"], state)
        replayed = {"decision": out["decision"], "checks": _signature(out["checks"])}
        return {"seq": seq, "match": replayed == stored, "engine": True, "stored": stored, "replayed": replayed}
