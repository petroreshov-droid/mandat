import asyncio
import json
import socket
import subprocess
import sys
import time

import pytest

from mandat.fixtures import demo_mandate
from mandat.gateway import MandateRejected
from mandat.mcp_server import AGENT_NAME, TOOL_NAMES, AgentTools, build_server, make_gateway

CLIENT_ONLY = {"confirm", "reject", "resume", "kill_switch", "sign_mandate", "pending_orders"}


@pytest.fixture
def tools():
    return AgentTools(make_gateway())


# --- функции инструментов (SDK не нужен) ---


def test_tool_names_follow_spec_3():
    assert TOOL_NAMES == (
        "get_portfolio",
        "get_instrument",
        "get_orderbook",
        "get_mandate",
        "post_order",
        "update_mandate",
    )
    assert not CLIENT_ONLY & set(TOOL_NAMES)
    assert not CLIENT_ONLY & {n for n in dir(AgentTools) if not n.startswith("_")}


def test_read_tools(tools):
    assert tools.get_portfolio()["cash_rub"] == 268653.0
    assert tools.get_instrument("TMOS")["lot"] == 1
    assert tools.get_orderbook("TMOS")["asks"][0] == [7.06, 250000]
    mandate = tools.get_mandate()
    assert mandate["mandate"] == demo_mandate() and "Продажи запрещены." in mandate["rules"]


def test_post_order_tool_runs_the_gateway_and_labels_the_agent(tools):
    ok = tools.post_order("SBER", "buy", 10, 305.5, order_id="M-1", reason="докупка")
    assert ok["decision"] == "ALLOW"
    denied = tools.post_order("SBER", "buy", 15)
    assert denied["decision"] == "DENY"
    assert [c["code"] for c in denied["checks"] if not c["ok"]] == ["LOT"]
    records = tools.gateway.journal.records
    assert [r["payload"]["agent"] for r in records if r["type"] == "decision"] == [AGENT_NAME, AGENT_NAME]
    assert records[1]["payload"]["order"]["reason"] == "докупка"


def test_ask_cannot_be_resolved_by_the_agent(tools):
    r = tools.post_order("YDEX", "buy", 1, 4121.0)
    assert r["decision"] == "ASK" and r["pending_id"]
    assert not hasattr(tools, "confirm") and not hasattr(tools, "resume") and not hasattr(tools, "kill_switch")
    assert tools.gateway.broker.orders == []


def test_update_mandate_tool_always_refuses(tools):
    r = tools.update_mandate({"allow_sell": True}, reason="надо")
    assert r["decision"] == "DENY" and r["checks"][0]["code"] == "MANDATE_CHANGE"


def test_server_refuses_to_start_with_a_critical_mandate():
    with pytest.raises(MandateRejected):
        make_gateway("t2_free")


def test_make_gateway_options(tmp_path):
    gw = make_gateway("t1_bonds", str(tmp_path / "j.jsonl"))
    assert gw.mandate["limits"]["daily_turnover"] is None  # исходный t1, без исправления
    assert (tmp_path / "j.jsonl").exists()
    assert make_gateway("demo").mandate["limits"]["daily_turnover"] == 100000


# --- настоящий MCP: SDK-сервер и клиент ---


@pytest.fixture(scope="module")
def sdk():
    """Официальный SDK 2.x. В 1.x через httpx тянется certifi (MPL-2.0), поэтому extra требует mcp>=2."""
    return pytest.importorskip("mcp.server.mcpserver")


def run(coro, timeout=40):
    return asyncio.run(asyncio.wait_for(coro, timeout))


def test_server_registers_exactly_the_agent_tools(sdk, tools):
    server = build_server(tools)
    listed = run(server.list_tools())
    assert [t.name for t in listed] == list(TOOL_NAMES)
    post = next(t for t in listed if t.name == "post_order")
    assert post.input_schema["required"] == ["ticker", "side", "quantity"]
    assert "ALLOW" in post.description


def test_call_tool_in_process(sdk, tools):
    server = build_server(tools)
    result = run(server.call_tool("post_order", {"ticker": "SBER", "side": "buy", "quantity": 15, "price": 305.5}))
    payload = json.loads(result.content[0].text)
    assert payload["decision"] == "DENY" and payload["explanation"] == "Отклонено: Количество 15 не кратно лоту 10."
    journal_size = len(tools.gateway.journal)
    with pytest.raises(Exception, match="quantity"):  # типы проверяет SDK до шлюза (по протоколу это ответ с is_error)
        run(server.call_tool("post_order", {"ticker": "SBER", "side": "buy", "quantity": "много"}))
    assert len(tools.gateway.journal) == journal_size  # до шлюза такой вызов не дошёл


def test_stdio_transport_speaks_mcp(sdk):
    from mcp.client import Client
    from mcp.client.stdio import StdioServerParameters

    async def session():
        params = StdioServerParameters(command=sys.executable, args=["-m", "mandat.mcp_server", "--transport", "stdio"])
        async with Client(params) as client:
            names = [t.name for t in (await client.list_tools()).tools]
            result = await client.call_tool(
                "post_order", {"ticker": "YDEX", "side": "buy", "quantity": 1, "price": 4121}
            )
            return names, json.loads(result.content[0].text)

    names, payload = run(session())
    assert names == list(TOOL_NAMES)
    assert payload["decision"] == "ASK" and payload["pending_id"] == "P0001"


def test_http_transport_speaks_mcp(sdk):
    from mcp.client import Client

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "mandat.mcp_server", "--transport", "http", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 30
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                break
            except OSError:
                assert time.time() < deadline and proc.poll() is None, "сервер не поднялся"
                time.sleep(0.1)

        async def session():
            async with Client(f"http://127.0.0.1:{port}/mcp") as client:
                result = await client.call_tool("get_mandate", {})
                return json.loads(result.content[0].text)

        payload = run(session())
        assert payload["title"] == "Облигации, фонд и реинвест" and payload["status"]["paused"] is False
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
