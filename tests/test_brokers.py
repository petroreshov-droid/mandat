import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mandat.brokers import MockBroker
from mandat.brokers.base import BrokerError
from mandat.brokers.tinvest_mcp import (
    TOOL_MAP,
    TInvestMcpBroker,
    TInvestMcpClient,
    ToolMappingError,
    parse_sse,
)
from mandat.fixtures import load_market

TOKEN = "t.SECRET-TOKEN-123"

# --- MockBroker ---


def order(**kw):
    return {"ticker": "SBER", "side": "buy", "quantity": 10, "price": None, **kw}


def test_mock_snapshot_is_a_copy():
    broker = MockBroker()
    snap = broker.snapshot()
    snap["cash_rub"] = 0
    snap["positions"]["SBER"] = 0
    assert broker.snapshot()["cash_rub"] == 268653.0 and broker.snapshot()["positions"]["SBER"] == 400


def test_buy_executes_at_limit_price_and_updates_cash_and_positions():
    broker = MockBroker()
    r = broker.place_order(order(price=305.5))
    assert r == {"order_id": "MOCK-000001", "status": "filled", "exec_price": 305.5, "reason": None}
    snap = broker.snapshot()
    assert snap["cash_rub"] == pytest.approx(268653 - 3055) and snap["positions"]["SBER"] == 410


def test_market_order_executes_at_last_price():
    broker = MockBroker()
    assert broker.place_order(order())["exec_price"] == 305.4


def test_sell_reduces_position_and_removes_empty_one():
    broker = MockBroker()
    broker.place_order(order(side="sell", quantity=400))
    snap = broker.snapshot()
    assert "SBER" not in snap["positions"] and snap["cash_rub"] == pytest.approx(268653 + 400 * 305.4)


def test_new_position_and_currency_conversion():
    broker = MockBroker()
    broker.place_order(order(ticker="SU52002RMFS1", quantity=1, price=984.5))
    snap = broker.snapshot()
    assert snap["positions"]["SU52002RMFS1"] == 1 and snap["cash_rub"] == pytest.approx(268653 - 984.5 * 11.2)


def test_ids_are_sequential_and_every_answer_is_logged():
    broker = MockBroker()
    ids = [broker.place_order(order())["order_id"] for _ in range(3)]
    assert ids == ["MOCK-000001", "MOCK-000002", "MOCK-000003"] and len(broker.orders) == 3


@pytest.mark.parametrize(
    ("override", "why"),
    [({"ticker": "NOPE"}, "не найдена"), ({"quantity": 15}, "лоту"), ({"quantity": 0}, "лоту")],
)
def test_exchange_rules_reject_without_changing_state(override, why):
    broker = MockBroker()
    r = broker.place_order(order(**override))
    assert r["status"] == "rejected" and why in r["reason"]
    assert broker.snapshot() == load_market()


def test_halted_instrument_is_rejected():
    market = load_market()
    market["instruments"]["SBER"]["status"] = "halted"
    assert MockBroker(market).place_order(order())["status"] == "rejected"


def test_without_exchange_rules_the_mock_accepts_any_lot():
    assert MockBroker(exchange_rules=False).place_order(order(quantity=15))["status"] == "filled"


def test_credit_cash():
    broker = MockBroker()
    broker.credit_cash(100)
    assert broker.snapshot()["cash_rub"] == 268753.0


# --- T-Invest MCP: клиент ---


class FakeServer:
    """Подставной транспорт: запоминает запросы и отвечает как сервер Streamable HTTP."""

    def __init__(self, call_result=None):
        self.requests = []
        self.call_result = call_result or {"content": [{"type": "text", "text": '{"ok": true}'}]}

    def __call__(self, url, headers, body):
        message = json.loads(body)
        self.requests.append((url, headers, message))
        method = message.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {}, "serverInfo": {"name": "fake"}}
            return 200, {"content-type": "application/json", "mcp-session-id": "sess-1"}, self._json(message, result)
        if method == "notifications/initialized":
            return 202, {}, b""
        if method == "tools/list":
            cursor = (message.get("params") or {}).get("cursor")
            page = (
                {"tools": [{"name": "b"}]}
                if cursor
                else {"tools": [{"name": "a", "inputSchema": {"required": ["x"]}}], "nextCursor": "c2"}
            )
            event = "event: message\ndata: " + self._json(message, page).decode() + "\n\n"
            return 200, {"content-type": "text/event-stream"}, event.encode()
        if method == "tools/call":
            return 200, {"content-type": "application/json"}, self._json(message, self.call_result)
        return (
            200,
            {"content-type": "application/json"},
            json.dumps(
                {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "нет метода"}}
            ).encode(),
        )

    @staticmethod
    def _json(message, result):
        return json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}).encode()


def client(server=None, **kw):
    return TInvestMcpClient(token=TOKEN, transport=server or FakeServer(), **kw)


def test_handshake_headers_and_session():
    server = FakeServer()
    c = client(server)
    tools = c.list_tools()
    assert [t["name"] for t in tools] == ["a", "b"]  # две страницы, ответ первой - SSE
    methods = [m.get("method") for _, _, m in server.requests]
    assert methods == ["initialize", "notifications/initialized", "tools/list", "tools/list"]
    first, later = server.requests[0][1], server.requests[2][1]
    assert first["Authorization"] == f"Bearer {TOKEN}"
    assert first["Accept"] == "application/json, text/event-stream" and first["Content-Type"] == "application/json"
    assert "Mcp-Session-Id" not in first and later["Mcp-Session-Id"] == "sess-1"
    assert later["MCP-Protocol-Version"] == "2025-03-26"  # согласованная сервером версия
    assert server.requests[0][2]["params"]["clientInfo"]["name"] == "mandat"


def test_initialize_happens_once():
    server = FakeServer()
    c = client(server)
    c.call("t")
    c.call("t")
    assert [m.get("method") for _, _, m in server.requests].count("initialize") == 1


def test_call_result_variants():
    assert client(FakeServer({"content": [{"type": "text", "text": '{"a": 1}'}]})).call("t", {"x": 1}) == {"a": 1}
    assert client(FakeServer({"content": [{"type": "text", "text": "просто текст"}]})).call("t") == "просто текст"
    structured = {"content": [], "structuredContent": {"price": 7.05}}
    assert client(FakeServer(structured)).call("t") == {"price": 7.05}
    server = FakeServer()
    client(server).call("get", {"ticker": "TMOS"})
    assert server.requests[-1][2]["params"] == {"name": "get", "arguments": {"ticker": "TMOS"}}


def test_tool_error_raises_broker_error():
    failing = FakeServer({"isError": True, "content": [{"type": "text", "text": "Недостаточно средств"}]})
    with pytest.raises(BrokerError, match="Недостаточно средств"):
        client(failing).call("post")


def test_json_rpc_error_raises_broker_error():
    c = client()
    c.initialize()
    with pytest.raises(BrokerError, match="нет метода"):
        c._rpc("unknown/method")


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failures_do_not_leak_the_token(status):
    c = client(lambda url, headers, body: (status, {}, b"denied"))
    with pytest.raises(BrokerError) as exc:
        c.list_tools()
    assert TOKEN not in str(exc.value) and "токен" in str(exc.value)


def test_server_errors_are_reported_with_status():
    with pytest.raises(BrokerError, match="HTTP 500"):
        client(lambda url, headers, body: (500, {}, b"boom")).list_tools()


def test_token_is_not_in_repr():
    assert TOKEN not in repr(client()) and TOKEN not in str(vars(client()).get("url"))


def test_https_required_except_for_loopback():
    with pytest.raises(BrokerError, match="https"):
        TInvestMcpClient(url="http://example.com/mcp", token=TOKEN, transport=FakeServer())
    TInvestMcpClient(url="http://127.0.0.1:9/mcp", token=TOKEN, transport=FakeServer())


def test_token_comes_from_environment(monkeypatch):
    monkeypatch.delenv("TINVEST_TOKEN", raising=False)
    with pytest.raises(BrokerError, match="TINVEST_TOKEN"):
        TInvestMcpClient(transport=FakeServer())
    monkeypatch.setenv("TINVEST_TOKEN", "from-env")
    server = FakeServer()
    TInvestMcpClient(transport=server).list_tools()
    assert server.requests[0][1]["Authorization"] == "Bearer from-env"


def test_parse_sse_handles_multiline_data_and_garbage():
    text = 'event: message\ndata: {"id": 1,\ndata: "x": 2}\n\n: comment\ndata: not json\n\ndata: {"id": 3}'
    assert parse_sse(text) == [{"id": 1, "x": 2}, {"id": 3}]


def test_default_transport_over_loopback_http():
    """Настоящий urllib-транспорт против локального сервера: SSE, сессия, заголовки."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((body.get("method"), self.headers.get("Authorization"), self.headers.get("Mcp-Session-Id")))
            if "id" not in body:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            result = (
                {"tools": [{"name": "ping"}]} if body["method"] == "tools/list" else {"protocolVersion": "2025-06-18"}
            )
            payload = (
                "event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}) + "\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Mcp-Session-Id", "loop-1")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        c = TInvestMcpClient(url=f"http://127.0.0.1:{server.server_port}/mcp", token=TOKEN)
        assert c.list_tools() == [{"name": "ping"}]
    finally:
        server.shutdown()
        server.server_close()
    assert seen[0] == ("initialize", f"Bearer {TOKEN}", None)
    assert seen[-1] == ("tools/list", f"Bearer {TOKEN}", "loop-1")


def test_connection_failure_is_a_broker_error_without_token():
    c = TInvestMcpClient(url="http://127.0.0.1:1/mcp", token=TOKEN, timeout=2)
    with pytest.raises(BrokerError) as exc:
        c.list_tools()
    assert TOKEN not in str(exc.value)


# --- T-Invest MCP: адаптер брокера ---


def test_tool_map_is_a_single_explicit_dict_waiting_for_the_sandbox():
    assert set(TOOL_MAP) == {"portfolio", "instrument", "last_price", "orderbook", "fx", "post_order"}


def test_broker_refuses_while_tool_names_are_unknown():
    broker = TInvestMcpBroker(client(), tool_map={k: None for k in TOOL_MAP})
    with pytest.raises(ToolMappingError, match="(?i)проверить на песочнице"):
        broker.snapshot()
    with pytest.raises(ToolMappingError, match="post_order"):
        broker.place_order({"ticker": "SBER", "side": "buy", "quantity": 10})
    assert issubclass(ToolMappingError, NotImplementedError)


def test_broker_does_not_guess_response_formats_even_when_names_are_given():
    named = {k: f"tool_{k}" for k in TOOL_MAP}
    broker = TInvestMcpBroker(client(), tool_map=named)
    with pytest.raises(NotImplementedError, match="разбор ответов"):
        broker.snapshot()


def test_tools_listing_cli(capsys, monkeypatch):
    from mandat.brokers import tinvest_mcp

    assert tinvest_mcp.main(client()) == 0
    out = capsys.readouterr().out
    assert "a  [x]" in out and "\nb  [-]" in out

    monkeypatch.delenv("TINVEST_TOKEN", raising=False)
    assert tinvest_mcp.main() == 1
    assert "TINVEST_TOKEN" in capsys.readouterr().err
