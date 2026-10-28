"""MCP-сервер Мандата: инструменты агента поверх шлюза и mock-брокера.

Запуск: python -m mandat.mcp_server --transport stdio|http --port 8765
Нужен пакет mcp: pip install "mandat[mcp]". Клиентские действия (подтверждение, пауза, подпись мандата)
агенту недоступны и в MCP не выставлены.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from .brokers import MockBroker
from .fixtures import demo_mandate, load_mandate
from .gateway import Gateway
from .journal import Journal

AGENT_NAME = "mcp-agent"
INSTRUCTIONS = (
    "Вы торгуете по мандату клиента. Каждая заявка проходит проверку шлюза: ALLOW - отправлена брокеру, "
    "ASK - ждёт подтверждения клиента (pending_id), DENY - отклонена, причина в explanation. "
    "Мандат изменить нельзя: попытка засчитывается нарушением, три нарушения за 10 минут ставят вас на паузу. "
    "Количество в заявках - в штуках, не в лотах. Начните с get_mandate."
)


class AgentTools:
    """Инструменты, видимые агенту (SPEC 3). Тонкая обёртка над шлюзом: правила живут в шлюзе."""

    def __init__(self, gateway: Gateway, agent: str = AGENT_NAME):
        self.gateway, self.agent = gateway, agent

    def get_portfolio(self) -> dict[str, Any]:
        """Портфель клиента: свободные рубли, позиции, их стоимость и доли."""
        return self.gateway.get_portfolio()

    def get_instrument(self, ticker: str) -> dict[str, Any]:
        """Карточка бумаги: класс, лот, валюта, режим торгов, последняя цена."""
        return self.gateway.get_instrument(ticker)

    def get_orderbook(self, ticker: str) -> dict[str, Any]:
        """Стакан заявок по бумаге: asks (продажа) и bids (покупка), пары [цена, лоты]."""
        return self.gateway.get_orderbook(ticker)

    def get_mandate(self) -> dict[str, Any]:
        """Мандат клиента простыми словами, подпись и текущий статус агента (пауза, остаток дневного лимита)."""
        return self.gateway.get_mandate()

    def post_order(
        self,
        ticker: str,
        side: str,
        quantity: int,
        price: float | None = None,
        order_id: str | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        """Заявка. side: buy или sell; quantity - штук (не лотов); price - лимитная цена или null для рыночной;
        order_id - ваш уникальный номер, повтор отклоняется. Ответ: decision ALLOW, ASK или DENY и объяснение."""
        return self.gateway.post_order(ticker, side, quantity, price, order_id, agent=self.agent, reason=reason)

    def update_mandate(self, changes: dict[str, Any] | None = None, reason: str = "") -> dict[str, Any]:
        """Просьба изменить мандат. Агенту всегда отказ: мандат меняет только клиент подписью новой версии."""
        return self.gateway.update_mandate(changes, agent=self.agent, reason=reason)


TOOL_NAMES = ("get_portfolio", "get_instrument", "get_orderbook", "get_mandate", "post_order", "update_mandate")


def _server_class() -> Any:
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:  # нет пакета или стоит mcp 1.x: в нём через httpx тянется certifi под MPL-2.0
        raise SystemExit('Нужен пакет mcp 2.x: pip install "mandat[mcp]"') from exc
    return MCPServer


def build_server(tools: AgentTools) -> Any:
    server = _server_class()("mandat", instructions=INSTRUCTIONS)
    for name in TOOL_NAMES:
        method = getattr(tools, name)
        server.tool(name=name, description=method.__doc__)(method)
    return server


def make_gateway(mandate_name: str | None = None, journal_path: str | None = None) -> Gateway:
    mandate = demo_mandate() if mandate_name in (None, "demo") else load_mandate(mandate_name)
    return Gateway(mandate, MockBroker(), journal=Journal(journal_path) if journal_path else None)


def serve(server: Any, transport: str, host: str, port: int) -> None:
    if transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=host, port=port)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mandat.mcp_server", description=__doc__)
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="адрес для http (в контейнере 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--mandate", help="demo (по умолчанию), t1_bonds, t2_free или путь к JSON")
    parser.add_argument("--journal", help="файл журнала JSONL")
    args = parser.parse_args(argv)
    gateway = make_gateway(args.mandate, args.journal)
    if args.transport == "http":
        print(f"Мандат: MCP на http://{args.host}:{args.port}/mcp (mock-брокер)", file=sys.stderr)
    serve(build_server(AgentTools(gateway)), args.transport, args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
