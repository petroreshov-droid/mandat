"""Зонд песочницы T-Invest MCP: что умеет сервер, какие у инструментов схемы и сколько занимает вызов.

Нужен, чтобы заполнить TOOL_MAP в mandat/brokers/tinvest_mcp.py не по догадкам и проверить главный вопрос:
требует ли сервер подтверждения клиента на заявку агента.

Запуск:
    .venv/bin/python -m bench.sandbox_probe --token-file ~/Downloads/tbank_sandbox.txt
    .venv/bin/python -m bench.sandbox_probe --token-file ... --call ИМЯ_ИНСТРУМЕНТА --args '{"ключ": "значение"}'

Токен читается из файла и никуда не печатается. Список инструментов сохраняется в bench/results/sandbox_tools.json.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

from mandat.brokers.base import BrokerError
from mandat.brokers.tinvest_mcp import SANDBOX_URL, TInvestMcpClient

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CA = ROOT / "certs" / "tbank_ca_bundle.pem"
RESULTS = Path(__file__).parent / "results"
# По этим словам в имени или описании ищем инструменты, которые двигают деньги: их вызываем только вручную.
WRITE_HINTS = (
    "order",
    "заявк",
    "cancel",
    "отмен",
    "transfer",
    "перевод",
    "пополн",
    "pay",
    "withdraw",
    "вывод",
    "replace",
)
LATENCY_RUNS = 10


def read_token(path: Path) -> str:
    token = path.expanduser().read_text(encoding="utf-8").strip()
    if not token or any(ch.isspace() for ch in token):
        raise SystemExit(f"В файле {path} должен быть один токен без пробелов и переносов внутри")
    return token


def is_write_tool(tool: dict[str, Any]) -> bool:
    hints = tool.get("annotations") or {}
    if hints.get("readOnlyHint") is True:
        return False
    if hints.get("destructiveHint") is True:
        return True
    text = f"{tool.get('name', '')} {tool.get('description', '')}".lower()
    return any(word in text for word in WRITE_HINTS)


def describe(tool: dict[str, Any]) -> str:
    schema = tool.get("inputSchema") or {}
    required = schema.get("required") or []
    optional = [k for k in (schema.get("properties") or {}) if k not in required]
    mark = "ПИШЕТ" if is_write_tool(tool) else "читает"
    desc = " ".join((tool.get("description") or "").split())[:160]
    return f"[{mark}] {tool['name']}\n      обязательные: {', '.join(required) or '-'}; необязательные: {', '.join(optional) or '-'}\n      {desc}"


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(share * (len(ordered) - 1))))]


def measure(client: TInvestMcpClient, runs: int = LATENCY_RUNS) -> dict[str, float]:
    """Задержка tools/list: сетевой путь до MCP-сервера без нагрузки на торговый контур."""
    samples = []
    for _ in range(runs):
        started = time.perf_counter()
        client.list_tools()
        samples.append((time.perf_counter() - started) * 1000)
    return {"n": runs, "p50_ms": round(statistics.median(samples), 1), "p95_ms": round(percentile(samples, 0.95), 1)}


def probe(client: TInvestMcpClient, out_dir: Path = RESULTS) -> dict[str, Any]:
    started = time.perf_counter()
    info = client.initialize()
    init_ms = round((time.perf_counter() - started) * 1000, 1)
    tools = client.list_tools()
    report = {
        "url": client.url,
        "server": info.get("serverInfo"),
        "protocol": info.get("protocolVersion"),
        "instructions": info.get("instructions"),
        "capabilities": info.get("capabilities"),
        "initialize_ms": init_ms,
        "tools_list_latency": measure(client),
        "write_tools": [t["name"] for t in tools if is_write_tool(t)],
        "read_tools": [t["name"] for t in tools if not is_write_tool(t)],
        "tools": tools,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sandbox_tools.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Зонд песочницы T-Invest MCP")
    ap.add_argument("--token-file", required=True, type=Path, help="файл с токеном песочницы")
    ap.add_argument("--url", default=SANDBOX_URL)
    ap.add_argument("--ca-bundle", default=str(DEFAULT_CA), help="корневые сертификаты Минцифры")
    ap.add_argument("--call", help="вызвать один инструмент и показать ответ")
    ap.add_argument("--args", default="{}", help="аргументы инструмента в JSON")
    args = ap.parse_args(argv)
    client = TInvestMcpClient(url=args.url, token=read_token(args.token_file), ca_bundle=args.ca_bundle)
    try:
        if args.call:
            started = time.perf_counter()
            result = client.call(args.call, json.loads(args.args))
            elapsed = round((time.perf_counter() - started) * 1000, 1)
            print(json.dumps(result, ensure_ascii=False, indent=1)[:6000])
            print(f"\n{args.call}: {elapsed} мс")
            return 0
        report = probe(client)
    except BrokerError as exc:
        print(f"Ошибка: {exc}")
        return 1
    print(f"Сервер: {report['server']}, протокол {report['protocol']}, initialize {report['initialize_ms']} мс")
    if report["instructions"]:
        print(f"Инструкции сервера агенту:\n{report['instructions'][:1500]}\n")
    lat = report["tools_list_latency"]
    print(f"tools/list: p50 {lat['p50_ms']} мс, p95 {lat['p95_ms']} мс ({lat['n']} вызовов)")
    print(f"Инструментов: {len(report['tools'])}, из них меняют счёт: {len(report['write_tools'])}\n")
    for tool in report["tools"]:
        print(describe(tool))
    print(f"\nПолные схемы: {RESULTS / 'sandbox_tools.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
