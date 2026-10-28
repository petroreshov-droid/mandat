"""Интерфейс брокера, за которым стоит шлюз."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

REJECTED_STATUSES = frozenset({"rejected", "error", "cancelled"})


class BrokerError(Exception):
    """Сбой связи или отказ брокера. Текст не должен содержать токены."""


class Broker(ABC):
    @abstractmethod
    def snapshot(self) -> dict[str, Any]:
        """Свежий рыночный снимок в формате SPEC 1.1: as_of, fx, cash_rub, instruments, positions, books."""

    @abstractmethod
    def place_order(self, order: dict[str, Any]) -> dict[str, Any]:
        """Отправляет заявку (формат SPEC 1.3). Возвращает {"order_id", "status", ...}."""
