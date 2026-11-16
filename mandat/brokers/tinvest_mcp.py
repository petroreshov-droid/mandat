"""Клиент официального T-Invest MCP по Streamable HTTP (только стандартная библиотека).

Песочница: https://sandbox-invest-public-api.tbank.ru/mcp. Токен - в переменной TINVEST_TOKEN (заголовок Bearer).
Сертификаты *.tbank.ru выпущены корневым центром Минцифры: путь к его сертификату - в TINVEST_CA_BUNDLE.

Счёт - в TINVEST_ACCOUNT_ID. Список инструментов сервера: python -m mandat.brokers.tinvest_mcp
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

from .. import __version__
from .base import Broker, BrokerError

SANDBOX_URL = "https://sandbox-invest-public-api.tbank.ru/mcp"
PROTOCOL_VERSION = "2025-06-18"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")

# Инструменты T-Invest MCP, которыми пользуется Мандат (имена и форматы проверены на песочнице 16.11.2026).
TOOL_MAP: dict[str, str] = {
    "positions": "invest_get_positions",  # свободные рубли и бумаги в штуках
    "find": "invest_find_instrument",  # тикер -> режим торгов, если бумаги нет в universe
    "share": "invest_get_share",  # карточки: лот, валюта, номинал, флаги
    "etf": "invest_get_etf",
    "bond": "invest_get_bond",
    "statuses": "invest_get_trading_statuses",  # принимает ли брокер заявки сейчас
    "last_price": "invest_get_market_values",  # последние цены и курсы валют
    "orderbook": "invest_get_orderbook",  # стакан
    "post_order": "invest_create_order",  # выставление заявки
}

# Режим торгов -> класс бумаги в мандате; порядок - какой режим брать, если у тикера их несколько.
BOARD_KIND = {
    "TQBR": "share",
    "TQPI": "share",
    "TQTF": "etf",
    "TQIF": "etf",
    "TQOB": "bond",
    "TQCB": "bond",
    "TQIR": "bond",
}
BOARD_ORDER = ("TQBR", "TQTF", "TQOB", "TQCB", "TQIR", "TQPI", "TQIF")
FX_INSTRUMENTS = {
    "cny": "CNYRUB_TOM_CETS",
    "usd": "USD000UTSTOM_CETS",
    "eur": "EUR_RUB__TOM_CETS",
    "hkd": "HKDRUB_TOM_CETS",
}
MAX_PARALLEL = 8  # одновременных вызовов MCP при снятии снимка
ORDER_STATUS = {
    "EXECUTION_REPORT_STATUS_FILL": "filled",
    "EXECUTION_REPORT_STATUS_PARTIALLYFILL": "partial",
    "EXECUTION_REPORT_STATUS_NEW": "placed",
    "EXECUTION_REPORT_STATUS_REJECTED": "rejected",
    "EXECUTION_REPORT_STATUS_CANCELLED": "cancelled",
}

Transport = Callable[[str, dict[str, str], bytes], "tuple[int, dict[str, str], bytes]"]


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
        self._lock = threading.Lock()  # снимок рынка вызывает инструменты параллельно
        self._init_lock = threading.Lock()

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
        with self._lock:
            self._next_id += 1
            message_id = self._next_id
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": message_id, "method": method}
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

    def _ensure_ready(self) -> None:
        if self._ready:
            return
        with self._init_lock:
            if not self._ready:
                self.initialize()

    def list_tools(self) -> list[dict[str, Any]]:
        self._ensure_ready()
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
        self._ensure_ready()
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
    """Брокер Мандата поверх T-Invest MCP: снимок рынка в формате SPEC 1.1 и выставление заявки.

    Форматы аргументов и ответов сверены с песочницей 16.11.2026 (сервер t-invest-mcp-server 1.53.0):
    - количество в заявке - в лотах, у нас - в штуках: переводим по лоту из карточки бумаги;
    - цены облигаций в стакане и котировках - в процентах от номинала («pt.»), у нас - в рублях за бумагу;
    - у invest_create_order нет ключа идемпотентности и нет шага подтверждения: повтор - вторая сделка;
    - confirmMarginTrade обязателен: Мандат всегда передаёт false, согласия на плечо агент дать не может.
    НКД облигаций в стоимость заявки не входит (как и в SPEC 1.1): банк спишет его сверху.
    """

    def __init__(
        self,
        client: TInvestMcpClient | None = None,
        account_id: str | None = None,
        universe: dict[str, str] | None = None,
        tool_map: dict[str, str] | None = None,
        book_depth: int = 20,
    ):
        self.client = client or TInvestMcpClient(ca_bundle=os.environ.get("TINVEST_CA_BUNDLE"))
        self.account_id = account_id or os.environ.get("TINVEST_ACCOUNT_ID")
        if not self.account_id:
            raise BrokerError("Не задан счёт: account_id или переменная TINVEST_ACCOUNT_ID")
        self.tools = {**TOOL_MAP, **(tool_map or {})}
        self.universe = dict(universe or {})  # тикер -> TICKER_CLASSCODE: бумаги, которые всегда в снимке
        self.book_depth = book_depth
        self._cards: dict[str, dict[str, Any]] = {}  # статичные данные бумаг кешируются на всю сессию
        self.calls: list[tuple[str, float, float, float]] = []  # (инструмент, мс, начало, конец) для бенчмарка
        self.unavailable: dict[str, str] = {}  # тикеры из universe, которых брокер не знает

    # --- вызовы MCP ---

    def _call(self, key: str, args: dict[str, Any]) -> Any:
        started = time.perf_counter()
        try:
            return self.client.call(self.tools[key], args)
        finally:
            ended = time.perf_counter()
            self.calls.append((self.tools[key], (ended - started) * 1000, started, ended))

    def _resolve(self, ticker: str) -> str:
        """Тикер -> TICKER_CLASSCODE: из universe, иначе поиск, основной режим торгов с доступом через API."""
        if ticker in self.universe:
            return self.universe[ticker]
        found = self._call("find", {"query": ticker, "responseView": ["IDENTIFIERS", "TRADING_FLAGS"]})
        matches = [i for i in (found or {}).get("instruments", []) if i.get("ticker") == ticker]
        matches.sort(key=lambda i: (not i.get("apiTradeAvailableFlag"), _board_rank(i.get("classCode", ""))))
        if not matches:
            raise BrokerError(f"Бумага {ticker} не найдена у брокера")
        self.universe[ticker] = f"{ticker}_{matches[0]['classCode']}"
        return self.universe[ticker]

    def _card(self, ticker: str) -> dict[str, Any]:
        if ticker not in self._cards:
            instrument_id = self._resolve(ticker)
            board = instrument_id.rsplit("_", 1)[1]
            kind = BOARD_KIND.get(board, "share")
            view = ["IDENTIFIERS", "STATIC_DATA", "TRADING_FLAGS"]
            data = self._call(
                kind, {"instrumentId": instrument_id, "idType": "INSTRUMENT_ID_TYPE_ID", "responseView": view}
            )
            raw = (data or {}).get("instrument") or {}
            if not raw.get("lot"):
                raise BrokerError(f"Брокер не вернул карточку {instrument_id}")
            card = {
                "name": raw.get("name", ticker),
                "class": kind,
                "lot": int(raw["lot"]),
                "currency": str(raw.get("currency", "rub")).lower(),
                "qual_only": bool(raw.get("forQualInvestorFlag", False)),
                "api_trade": bool(raw.get("apiTradeAvailableFlag", False)),
                "instrument_id": instrument_id,
            }
            if kind == "bond":
                card["nominal"] = _num((raw.get("nominal") or {}).get("value")) or 1000.0
            self._cards[ticker] = card
        return self._cards[ticker]

    # --- интерфейс Broker ---

    def snapshot(self) -> dict[str, Any]:
        """Позиции, статусы торгов и цены запрашиваются одновременно: задержка снимка - самый медленный вызов."""
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
            fut_positions = pool.submit(self._call, "positions", {"accountId": self.account_id})
            instruments = self._load_cards(pool, list(self.universe), held=set())
            fut_market = pool.submit(self._market, instruments)
            positions_raw = fut_positions.result() or {}
            cash = sum(
                _num(m.get("value")) for m in positions_raw.get("money", []) if str(m.get("currency")).lower() == "rub"
            )
            positions: dict[str, float] = {}
            for sec in positions_raw.get("securities", []):
                ticker, qty = sec.get("ticker"), _num(sec.get("balance"))
                if ticker and qty:
                    self.universe.setdefault(ticker, f"{ticker}_{sec.get('classCode')}")
                    positions[ticker] = int(qty) if qty == int(qty) else qty
            missing_held = [t for t in positions if t in self.unavailable]
            if missing_held:  # без цены бумаги на счёте доли портфеля не посчитать: проверку не проводим
                raise BrokerError(f"Нет данных брокера по бумаге на счёте: {', '.join(missing_held)}")
            fut_market.result()
            extra = [t for t in positions if t not in instruments]  # бумаги на счёте вне universe: второй круг
            if extra:
                more = self._load_cards(pool, extra, held=set(extra))
                self._market(more)
                instruments.update(more)
        currencies = {i["currency"] for i in instruments.values()} - {"rub"}
        return {
            "as_of": datetime.now(UTC).isoformat(timespec="seconds"),
            "fx": {"rub": 1.0, **self._fx(currencies)},
            "cash_rub": cash,
            "instruments": instruments,
            "positions": positions,
            "books": _LazyBooks(self, instruments),
        }

    def _load_cards(self, pool: ThreadPoolExecutor, tickers: list[str], held: set[str]) -> dict[str, dict[str, Any]]:
        """Карточки из кеша, недостающие - параллельно. Бумагу из списка клиента, которой нет у брокера, пропускаем."""
        futures = {t: pool.submit(self._card, t) for t in tickers if t not in self.unavailable or t in held}
        out: dict[str, dict[str, Any]] = {}
        for ticker, fut in futures.items():
            try:
                out[ticker] = dict(fut.result())
            except BrokerError as exc:
                if ticker in held:
                    raise
                self.unavailable[ticker] = str(exc)[:200]
        return out

    def _market(self, instruments: dict[str, dict[str, Any]]) -> None:
        """Статус торгов и последняя цена: по одному вызову на все бумаги, оба одновременно."""
        if not instruments:
            return
        ids = {i["instrument_id"]: t for t, i in instruments.items()}
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_st = pool.submit(self._call, "statuses", {"instrumentId": list(ids)})
            fut_mv = pool.submit(self._call, "last_price", {"instrumentId": list(ids)})
            statuses, values = fut_st.result() or {}, fut_mv.result() or {}
        for st in statuses.get("tradingStatuses", []):
            ticker = ids.get(f"{st.get('ticker')}_{st.get('classCode')}")
            if ticker:
                instruments[ticker]["exchange_status"] = st.get("tradingStatus", "")
                instruments[ticker]["status"] = _status(st, instruments[ticker]["api_trade"])
        for item in values.get("instruments", []):
            ticker = ids.get(f"{item.get('ticker')}_{item.get('classCode')}")
            last = _last(item)
            if ticker and last is not None:
                instruments[ticker]["last"] = self._to_money(instruments[ticker], last)
        for inst in instruments.values():  # нет статуса или цены - торговать по бумаге нельзя
            inst.setdefault("status", "halted")
            inst.setdefault("exchange_status", "нет данных")
            if "last" not in inst:
                inst["last"] = 0.0
                inst["status"] = "halted"

    def _fx(self, currencies: set[str]) -> dict[str, float]:
        if not currencies:
            return {}
        ids = {FX_INSTRUMENTS[c]: c for c in currencies if c in FX_INSTRUMENTS}
        unknown = currencies - set(ids.values())
        if unknown:
            raise BrokerError(f"Нет курса к рублю для валют: {', '.join(sorted(unknown))}")
        values = self._call("last_price", {"instrumentId": list(ids)}) or {}
        rates = {}
        for item in values.get("instruments", []):
            cur = ids.get(f"{item.get('ticker')}_{item.get('classCode')}")
            last = _last(item)
            if cur and last:
                rates[cur] = last
        missing = set(ids.values()) - set(rates)
        if missing:
            raise BrokerError(f"Брокер не вернул курс: {', '.join(sorted(missing))}")
        return rates

    def orderbook(self, ticker: str, inst: dict[str, Any]) -> dict[str, list[list[float]]]:
        raw = self._call("orderbook", {"instrumentId": inst["instrument_id"], "depth": self.book_depth}) or {}

        def side(levels: list[dict[str, Any]]) -> list[list[float]]:
            return [[self._to_money(inst, _num(lv["price"]["value"])), _num(lv["quantity"])] for lv in levels]

        return {"asks": side(raw.get("asks", [])), "bids": side(raw.get("bids", []))}

    @staticmethod
    def _to_money(inst: dict[str, Any], price: float) -> float:
        """Цена брокера -> цена за 1 бумагу в валюте бумаги (облигации котируются в % от номинала)."""
        return round(price * inst["nominal"] / 100, 6) if inst["class"] == "bond" else price

    def place_order(self, order: dict[str, Any]) -> dict[str, Any]:
        ticker, qty, price = order["ticker"], order["quantity"], order.get("price")
        inst = self._card(ticker)
        if qty <= 0 or qty % inst["lot"]:
            return {"order_id": None, "status": "rejected", "exec_price": None, "reason": "Количество не кратно лоту"}
        args: dict[str, Any] = {
            "accountId": self.account_id,
            "instrumentId": inst["instrument_id"],
            "direction": "ORDER_DIRECTION_BUY" if order["side"] == "buy" else "ORDER_DIRECTION_SELL",
            "quantity": int(qty // inst["lot"]),
            "confirmMarginTrade": False,
        }
        if price is None:
            args["orderType"] = "ORDER_TYPE_MARKET"
        else:
            args["orderType"] = "ORDER_TYPE_LIMIT"
            if inst["class"] == "bond":
                args["price"] = {"value": _dec(price / inst["nominal"] * 100)}
                args["priceType"] = "PRICE_TYPE_POINT"
            else:
                args["price"] = {"value": _dec(price)}
                args["priceType"] = "PRICE_TYPE_CURRENCY"
        try:
            res = self._call("post_order", args) or {}
        except BrokerError as exc:
            if "INVALID_ARGUMENT" in str(exc) or "FAILED_PRECONDITION" in str(exc):
                return {"order_id": None, "status": "rejected", "exec_price": None, "reason": str(exc)[:300]}
            raise  # сеть или внутренняя ошибка: исход неизвестен, шлюз пишет «error»
        raw_status = res.get("executionReportStatus", "")
        executed = (res.get("executedOrderPrice") or {}).get("value")
        return {
            "order_id": res.get("orderId"),
            "status": ORDER_STATUS.get(raw_status, "unknown"),
            "exec_price": _num(executed) if executed is not None else None,
            "lots_executed": int(_num(res.get("lotsExecuted"))),
            "broker_status": raw_status,
            "reason": None,
        }


class _LazyBooks(dict):
    """Стаканы по запросу: шлюзу нужен стакан только той бумаги, по которой пришла заявка."""

    def __init__(self, broker: TInvestMcpBroker, instruments: dict[str, dict[str, Any]]):
        super().__init__()
        self._broker, self._instruments = broker, instruments

    def _load(self, ticker: str) -> dict[str, Any] | None:
        if not dict.__contains__(self, ticker):
            inst = self._instruments.get(ticker)
            if inst is None:
                return None
            dict.__setitem__(self, ticker, self._broker.orderbook(ticker, inst))
        return dict.__getitem__(self, ticker)

    def get(self, ticker: str, default: Any = None) -> Any:  # type: ignore[override]
        book = self._load(ticker)
        return default if book is None else book

    def __getitem__(self, ticker: str) -> Any:
        book = self._load(ticker)
        if book is None:
            raise KeyError(ticker)
        return book

    def __contains__(self, ticker: object) -> bool:
        return isinstance(ticker, str) and ticker in self._instruments


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _last(item: dict[str, Any]) -> float | None:
    for v in item.get("values", []):
        if v.get("type") == "INSTRUMENT_VALUE_LAST_PRICE":
            return _num(v["value"]["value"])
    return None


def _dec(value: float) -> str:
    return format(Decimal(repr(round(value, 9))).normalize(), "f")


def _board_rank(class_code: str) -> int:
    return BOARD_ORDER.index(class_code) if class_code in BOARD_ORDER else len(BOARD_ORDER)


def _status(st: dict[str, Any], api_trade: bool) -> str:
    """normal: брокер сейчас принимает заявки по бумаге через API; closed: торгов нет; halted: остальное."""
    if not api_trade:
        return "halted"
    if st.get("limitOrderAvailableFlag") or st.get("marketOrderAvailableFlag"):
        return "normal"
    raw = st.get("tradingStatus", "")
    return "closed" if any(k in raw for k in ("NOT_AVAILABLE", "CLOSING_PERIOD", "OPENING_PERIOD")) else "halted"


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
