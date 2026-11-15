"""Зонд песочницы: разбор инструментов, сохранение отчёта, токен не утекает в вывод и в файл."""

import json

import pytest

from bench import sandbox_probe
from mandat.brokers.tinvest_mcp import TInvestMcpClient

TOKEN = "t.secret-sandbox-token-000"
TOOLS = [
    {"name": "get_portfolio", "description": "Портфель клиента", "inputSchema": {"required": ["account_id"]}},
    {"name": "post_order", "description": "Выставить заявку", "inputSchema": {"required": ["ticker", "quantity"]}},
    {"name": "find_orders", "description": "Список активных заявок", "annotations": {"readOnlyHint": True}},
    {"name": "top_up", "description": "Пополнение счёта с карты", "inputSchema": {"properties": {"amount": {}}}},
]


class FakeSandbox:
    def __init__(self):
        self.calls = []

    def __call__(self, url, headers, body):
        message = json.loads(body)
        self.calls.append(message.get("method"))
        method = message.get("method")
        if method == "notifications/initialized":
            return 202, {}, b""
        result = {
            "initialize": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "t-invest-mcp", "version": "1.0"},
                "instructions": "Перед заявкой покажи параметры пользователю.",
            },
            "tools/list": {"tools": TOOLS},
            "tools/call": {"content": [{"type": "text", "text": '{"status": "ok"}'}]},
        }[method]
        payload = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}).encode()
        return 200, {"content-type": "application/json"}, payload


def test_write_tools_are_recognised_by_hints_and_keywords():
    marks = {t["name"]: sandbox_probe.is_write_tool(t) for t in TOOLS}
    assert marks == {"get_portfolio": False, "post_order": True, "find_orders": False, "top_up": True}


def test_probe_saves_report_without_token(tmp_path):
    client = TInvestMcpClient(token=TOKEN, transport=FakeSandbox())
    report = sandbox_probe.probe(client, out_dir=tmp_path)
    saved = (tmp_path / "sandbox_tools.json").read_text(encoding="utf-8")
    assert report["write_tools"] == ["post_order", "top_up"]
    assert report["read_tools"] == ["get_portfolio", "find_orders"]
    assert report["instructions"].startswith("Перед заявкой")
    assert report["tools_list_latency"]["n"] == sandbox_probe.LATENCY_RUNS
    assert TOKEN not in saved and TOKEN not in json.dumps(report, ensure_ascii=False)


def test_describe_lists_required_and_optional_arguments():
    text = sandbox_probe.describe(TOOLS[3])
    assert "[ПИШЕТ] top_up" in text and "необязательные: amount" in text


def test_token_file_must_hold_exactly_one_token(tmp_path):
    good = tmp_path / "ok.txt"
    good.write_text(f"  {TOKEN}\n", encoding="utf-8")
    assert sandbox_probe.read_token(good) == TOKEN
    bad = tmp_path / "bad.txt"
    bad.write_text("first second", encoding="utf-8")
    with pytest.raises(SystemExit):
        sandbox_probe.read_token(bad)
