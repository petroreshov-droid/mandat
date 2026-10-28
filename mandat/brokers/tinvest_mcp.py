"""Клиент официального T-Invest MCP по Streamable HTTP (только стандартная библиотека).

Песочница: https://sandbox-invest-public-api.tbank.ru/mcp. Токен - в переменной TINVEST_TOKEN (заголовок Bearer).
Сертификаты *.tbank.ru выпущены корневым центром Минцифры: путь к его сертификату - в TINVEST_CA_BUNDLE.

Список инструментов сервера: python -m mandat.brokers.tinvest_mcp
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from .. import __version__
from .base import Broker, BrokerError

SANDBOX_URL = "https://sandbox-invest-public-api.tbank.ru/mcp"
PROTOCOL_VERSION = "2025-06-18"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")

# Соответствие «что нужно нашему интерфейсу брокера -> имя инструмента T-Invest MCP».
# Имена инструментов официального сервера и формат их аргументов и ответов НЕ известны:
# проверить на песочнице (python -m mandat.brokers.tinvest_mcp печатает список), заполнить значения, не угадывать.
TOOL_MAP: dict[str, str | None] = {
    "portfolio": None,  # позиции и свободные рубли
    "instrument": None,  # карточка бумаги: лот, валюта, класс, режим торгов, признак «для квалифицированных»
    "last_price": None,  # последняя цена
    "orderbook": None,  # стакан
    "fx": None,  # курсы валют к рублю
    "post_order": None,  # выставление заявки
}

Transport = Callable[[str, dict[str, str], bytes], "tuple[int, dict[str, str], bytes]"]


class ToolMappingError(NotImplementedError):
    """Для операции брокера не задано имя инструмента T-Invest MCP."""


def _urllib_transport(ca_bundle: str | None, timeout: float) -> Transport:
    context = ssl.create_default_context()
    if ca_bundle:
        context.load_verify_locations(cafile=ca_bundle)  # к системным корням добавляем корень Минцифры
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))

    def send(url: str, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, str], bytes]:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with opener.open(request, timeout=timeout) as response:
                return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BrokerError(f"T-Invest MCP недоступен: {type(exc).__name__}") from exc

    return send


def parse_sse(text: str) -> list[dict[str, Any]]:
    """JSON-сообщения из тела text/event-stream (строки data:, события разделены пустой строкой)."""
    chunks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            current.append(line[5:].removeprefix(" "))
        elif not line.strip() and current:
            chunks.append("\n".join(current))
            current = []
    if current:
        chunks.append("\n".join(current))
    messages = []
    for chunk in chunks:
        try:
            messages.append(json.loads(chunk))
        except json.JSONDecodeError:
            continue
    return messages


class TInvestMcpClient:
    def __init__(
        self,
        url: str = SANDBOX_URL,
        token: str | None = None,
        ca_bundle: str | None = None,
        *,
        timeout: float = 30.0,
        transport: Transport | None = None,
    ):
        parsed = urlparse(url)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS):
            raise BrokerError("Токен отправляется только по https (http допустим для localhost)")
        self.url = url
        self._token = token if token is not None else os.environ.get("TINVEST_TOKEN")
        if not self._token:
            raise BrokerError("Не задан токен: переменная окружения TINVEST_TOKEN")
        ca = ca_bundle if ca_bundle is not None else os.environ.get("TINVEST_CA_BUNDLE")
        self._transport = transport or _urllib_transport(ca, timeout)
        self._session_id: str | None = None
        self._protocol = PROTOCOL_VERSION
        self._next_id = 0
        self._ready = False

    def __repr__(self) -> str:  # токен в репрезентацию не попадает
        return f"TInvestMcpClient(url={self.url!r})"

    def _post(self, message: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self._token}",
            "MCP-Protocol-Version": self._protocol,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        status, reply_headers, body = self._transport(self.url, headers, json.dumps(message).encode("utf-8"))
        if status in (401, 403):
            raise BrokerError(f"T-Invest MCP: доступ запрещён (HTTP {status}), проверьте токен")
        if status >= 400:
            raise BrokerError(f"T-Invest MCP: HTTP {status}: {body[:200].decode('utf-8', 'replace')}")
        if "mcp-session-id" in reply_headers:
            self._session_id = reply_headers["mcp-session-id"]
        return status, reply_headers, body

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            message["params"] = params
        _, headers, body = self._post(message)
        if "text/event-stream" in headers.get("content-type", ""):
            replies = parse_sse(body.decode("utf-8"))
        else:
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError as exc:
                raise BrokerError(f"T-Invest MCP: ответ на {method} не JSON") from exc
            replies = parsed if isinstance(parsed, list) else [parsed]
        reply = next((r for r in replies if isinstance(r, dict) and r.get("id") == message["id"]), None)
        if reply is None:
            raise BrokerError(f"T-Invest MCP: нет ответа на {method}")
        if "error" in reply:
            raise BrokerError(f"T-Invest MCP, {method}: {reply['error'].get('message', reply['error'])}")
        return reply.get("result", {})

    def initialize(self) -> dict[str, Any]:
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mandat", "version": __version__},
            },
        )
        self._protocol = result.get("protocolVersion", PROTOCOL_VERSION)
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._ready = True
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        if not self._ready:
            self.initialize()
        tools: list[dict[str, Any]] = []
        cursor = None
        while True:
            result = self._rpc("tools/list", {"cursor": cursor} if cursor else None)
            tools += result.get("tools", [])
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call(self, tool: str, args: dict[str, Any] | None = None) -> Any:
        """Вызывает инструмент. Возвращает structuredContent, иначе разобранный JSON из текста, иначе текст."""
        if not self._ready:
            self.initialize()
        result = self._rpc("tools/call", {"name": tool, "arguments": args or {}})
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        if result.get("isError"):
            raise BrokerError(f"T-Invest MCP, {tool}: {' '.join(texts)[:300] or 'ошибка инструмента'}")
        if result.get("structuredContent") is not None:
            return result["structuredContent"]
        text = "\n".join(texts)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text


class TInvestMcpBroker(Broker):
    """Адаптер к T-Invest MCP. Пока нет соответствия инструментов (TOOL_MAP), операции честно отказывают."""

    def __init__(self, client: TInvestMcpClient | None = None, tool_map: dict[str, str | None] | None = None):
        self.client = client or TInvestMcpClient(ca_bundle=os.environ.get("TINVEST_CA_BUNDLE"))
        self.tool_map = dict(TOOL_MAP if tool_map is None else tool_map)

    def _require(self, *keys: str) -> None:
        missing = [k for k in keys if self.tool_map.get(k) is None]
        if missing:
            raise ToolMappingError(
                f"Не задано соответствие T-Invest MCP для: {', '.join(missing)}. "
                "Проверить на песочнице (TOOL_MAP в mandat/brokers/tinvest_mcp.py)."
            )
        raise NotImplementedError(
            "Соответствие имён задано, но аргументы и разбор ответов инструментов не написаны: "
            "схемы смотреть в list_tools() на песочнице."
        )

    def snapshot(self) -> dict[str, Any]:
        self._require("portfolio", "instrument", "last_price", "orderbook", "fx")
        raise AssertionError("недостижимо")

    def place_order(self, order: dict[str, Any]) -> dict[str, Any]:
        self._require("post_order")
        raise AssertionError("недостижимо")


def main(client: TInvestMcpClient | None = None) -> int:
    """Печатает инструменты сервера: имя, описание и обязательные аргументы."""
    try:
        for tool in (client or TInvestMcpClient()).list_tools():
            required = ", ".join(tool.get("inputSchema", {}).get("required", [])) or "-"
            print(f"{tool['name']}  [{required}]\n    {(tool.get('description') or '').strip()[:200]}")
    except BrokerError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
