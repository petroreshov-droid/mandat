"""Загрузка демо-данных из docs/data (рынок и два мандата)."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from .clock import parse_iso

DATA_DIR = Path(os.environ.get("MANDAT_DATA_DIR") or Path(__file__).resolve().parents[1] / "docs" / "data")
DEMO_DAILY_TURNOVER = 100000


def _load(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Нет файла {path}. Данные демо лежат в docs/data, другой путь - MANDAT_DATA_DIR"
        ) from exc


def load_market() -> dict[str, Any]:
    return _load(DATA_DIR / "market_demo.json")


def load_mandate(name: str) -> dict[str, Any]:
    """name: t1_bonds, t2_free или путь к JSON-файлу."""
    path = Path(name)
    if not path.suffix:
        path = DATA_DIR / "mandates" / f"{name}.json"
    return _load(path)


def all_mandates() -> dict[str, dict[str, Any]]:
    return {p.stem: _load(p) for p in sorted((DATA_DIR / "mandates").glob("*.json"))}


def demo_mandate() -> dict[str, Any]:
    """t1_bonds с исправленным L_NO_DAILY (daily_turnover = 100000): мандат из эталонных сценариев SPEC 7."""
    mandate = copy.deepcopy(load_mandate("t1_bonds"))
    mandate["limits"]["daily_turnover"] = DEMO_DAILY_TURNOVER
    return mandate


def demo_start() -> float:
    """Начало демо: as_of рыночного снимка, 10:00 10.11.2026 МСК."""
    return parse_iso(load_market()["as_of"])
