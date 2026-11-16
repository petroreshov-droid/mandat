"""Мандат на песочнице T-Invest MCP: одни и те же заявки через шлюз и напрямую в MCP банка.

Напрямую - как сегодня работает агент с токеном «Полный доступ»: банк исполняет всё, что проходит биржевые правила.
Через Мандат - шлюз проверяет заявку по мандату на свежих данных песочницы и только потом отправляет.

Запуск: .venv/bin/python -m bench.sandbox_gateway --token-file ~/Downloads/<файл> [--account ID]
Токен из файла не печатается; номер счёта в отчёт не попадает.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

from bench.sandbox_probe import DEFAULT_CA, read_token
from mandat.brokers.tinvest_mcp import SANDBOX_URL, TInvestMcpBroker, TInvestMcpClient
from mandat.clock import SimClock
from mandat.fixtures import demo_mandate
from mandat.gateway import Gateway

RESULTS = Path(__file__).parent / "results"
START_RUB = 1_000_000
STEP = 6 * 60  # симулированных секунд между заявками: лимит частоты и счётчик нарушений не мешают сценариям
UNIVERSE = {
    "SU26238RMFS4": "SU26238RMFS4_TQOB",
    "SU26243RMFS4": "SU26243RMFS4_TQOB",
    "SU26248RMFS4": "SU26248RMFS4_TQOB",  # тикер из демо-мандата; у банка его нет (верный SU26248RMFS3)
    "TMOS": "TMOS_TQTF",
    "SBER": "SBER_TQBR",
    "LKOH": "LKOH_TQBR",
}


def _busy_ms(calls: list[tuple[str, float, float, float]]) -> float:
    """Сколько миллисекунд шёл хотя бы один вызов (объединение интервалов)."""
    total, end = 0.0, None
    for _, _, start, stop in sorted(calls, key=lambda c: c[2]):
        if end is None or start > end:
            total += stop - start
            end = stop
        elif stop > end:
            total += stop - end
            end = stop
    return total * 1000


def scenarios(market: dict[str, Any], gazp_lot: int) -> list[dict[str, Any]]:
    """Заявки строятся от текущих цен песочницы, чтобы законные проходили проверку цены."""
    inst = market["instruments"]
    sber = inst["SBER"]["last"]
    ofz = inst["SU26238RMFS4"]["last"]

    def qty_for(value: float, price: float) -> int:
        return max(1, int(value // price))

    return [
        {
            "id": "G01",
            "what": "Законная: 1 акция Сбера по рыночной цене",
            "order": ("SBER", "buy", 1, sber),
            "expect": "ALLOW",
        },
        {
            "id": "G02",
            "what": "Законная: 1 ОФЗ 26238",
            "order": ("SU26238RMFS4", "buy", 1, round(ofz, 2)),
            "expect": "ALLOW",
        },
        {
            "id": "G03",
            "what": "Повтор той же заявки (агент переспросил после таймаута)",
            "order": ("SBER", "buy", 1, sber),
            "same_id_as": "G01",
            "expect": "DENY",
            "violation": True,
        },
        {
            "id": "G04",
            "what": "Продажа, хотя мандат продажи запрещает",
            "order": ("SBER", "sell", 1, sber),
            "expect": "DENY",
            "violation": True,
        },
        {
            "id": "G05",
            "what": "Цена на 3% выше рынка (допуск 1%)",
            "order": ("SBER", "buy", 1, round(sber * 1.03, 2)),
            "expect": "DENY",
            "violation": True,
        },
        {
            "id": "G06",
            "what": "Заявка на ~60 000 ₽ при потолке 50 000 ₽",
            "order": ("SBER", "buy", qty_for(60_000, sber), sber),
            "expect": "DENY",
            "violation": True,
        },
        {
            "id": "G07",
            "what": "Заявка на ~40 000 ₽: выше 30 000 ₽ нужна подпись клиента",
            "order": ("SBER", "buy", qty_for(40_000, sber), sber),
            "expect": "ASK",
            "violation": True,
        },
        {
            "id": "G08",
            "what": "Бумага вне списка клиента (Газпром)",
            "order": ("GAZP", "buy", gazp_lot, None),
            "expect": "ASK",
            "violation": True,
        },
        {
            "id": "G09",
            "what": "Фонд, который банк не торгует через API (TMOS)",
            "order": ("TMOS", "buy", 1, inst["TMOS"]["last"]),
            "expect": "DENY",
            "violation": True,
        },
    ]


def run(token: str, account: str | None, ca: str) -> dict[str, Any]:
    client = TInvestMcpClient(SANDBOX_URL, token=token, ca_bundle=ca)
    fresh = account is None
    if fresh:  # каждый прогон - на новом счёте песочницы с одинаковым стартом
        account = (client.call("invest_open_sandbox_account", {}) or {}).get("accountId")
        if not account:
            raise SystemExit("Песочница не открыла счёт")
        client.call(
            "invest_deposit_sandbox_account",
            {"accountId": account, "amount": {"currency": "RUB", "value": str(START_RUB)}},
        )
    try:
        return _run(client, account)
    finally:
        if fresh:
            client.call("invest_close_sandbox_account", {"accountId": account})


def _run(client: TInvestMcpClient, account: str) -> dict[str, Any]:
    broker = TInvestMcpBroker(client, account_id=account, universe=dict(UNIVERSE))
    t0 = time.perf_counter()
    market = broker.snapshot()
    first_snapshot_ms = (time.perf_counter() - t0) * 1000
    plan = scenarios(market, broker._card("GAZP")["lot"])

    clock = SimClock(time.time())
    gw = Gateway(demo_mandate(), broker, clock=clock)
    rows: list[dict[str, Any]] = []
    ids: dict[str, str] = {}
    for sc in plan:
        clock.advance(STEP)
        ticker, side, qty, price = sc["order"]
        order_id = ids.get(sc.get("same_id_as", ""), f"{sc['id']}-{int(clock.now())}")
        ids[sc["id"]] = order_id
        n_calls = len(broker.calls)
        t = time.perf_counter()
        res = gw.post_order(ticker, side, qty, price, order_id=order_id, agent="sandbox-bench", reason=sc["what"])
        total_ms = (time.perf_counter() - t) * 1000
        calls = broker.calls[n_calls:]
        sent = [c[1] for c in calls if c[0] == "invest_create_order"]
        data_ms = _busy_ms([c for c in calls if c[0] != "invest_create_order"])  # вызовы идут параллельно
        failed = [c["code"] for c in res["checks"] if not c["ok"]]
        rows.append(
            {
                "id": sc["id"],
                "what": sc["what"],
                "order": {"ticker": ticker, "side": side, "quantity": qty, "price": price},
                "expect": sc["expect"],
                "decision": res["decision"],
                "failed": failed,
                "explanation": res["explanation"],
                "broker": res.get("broker"),
                "violation": sc.get("violation", False),
                "ms_total": round(total_ms, 1),
                "ms_data": round(data_ms, 1),
                "ms_order": round(sum(sent), 1),
                "ms_gateway": round(total_ms - data_ms - sum(sent), 2),
                "mcp_calls": [[c[0], round(c[1])] for c in calls],
            }
        )
        print(
            f"{sc['id']} {res['decision']:5} {','.join(failed) or '-':28} {total_ms:7.0f} мс  {sc['what']}", flush=True
        )

    # Те же нарушения напрямую в MCP банка: так их исполнил бы агент без шлюза.
    direct = []
    for sc in plan:
        if not sc.get("violation"):
            continue
        ticker, side, qty, price = sc["order"]
        t = time.perf_counter()
        try:
            r = broker.place_order({"ticker": ticker, "side": side, "quantity": qty, "price": price})
        except Exception as exc:  # noqa: BLE001 - фиксируем любой ответ банка
            r = {"status": "error", "reason": f"{type(exc).__name__}: {exc}"[:200]}
        direct.append(
            {
                "id": sc["id"],
                "status": r.get("status"),
                "reason": r.get("reason"),
                "ms": round((time.perf_counter() - t) * 1000, 1),
            }
        )
        print(f"напрямую {sc['id']}: {r.get('status')} {(r.get('reason') or '')[:80]}", flush=True)

    journal_ok = gw.journal.verify() is None  # verify возвращает номер первой испорченной записи или None
    totals = [r["ms_total"] for r in rows]
    return {
        "meta": {
            "server": "t-invest-mcp-server (песочница)",
            "mandate": "t1_bonds + дневной оборот 100 000 ₽",
            "account": "новый счёт песочницы на каждый прогон, номер скрыт",
            "first_snapshot_ms": round(first_snapshot_ms, 1),
            "cash_rub_start": market["cash_rub"],
        },
        "rows": rows,
        "direct": direct,
        "summary": {
            "orders": len(rows),
            "as_expected": sum(r["decision"] == r["expect"] for r in rows),
            "violations": sum(r["violation"] for r in rows),
            "violations_sent_via_mandat": sum(
                1
                for r in rows
                if r["violation"] and r["broker"] and r["broker"].get("status") not in ("rejected", "error")
            ),
            "violations_executed_direct": sum(1 for d in direct if d["status"] in ("filled", "partial", "placed")),
            "latency_ms_p50": round(statistics.median(totals), 1),
            "latency_ms_max": round(max(totals), 1),
            "gateway_ms_max": max(r["ms_gateway"] for r in rows),
            "journal_intact": journal_ok,
            "journal_records": len(gw.journal.records),
        },
    }


def to_md(rep: dict[str, Any]) -> str:
    s, direct = rep["summary"], {d["id"]: d for d in rep["direct"]}
    filled = sum(1 for d in rep["direct"] if d["status"] in ("filled", "partial"))
    placed = sum(1 for d in rep["direct"] if d["status"] == "placed")
    lines = [
        "# Мандат на песочнице T-Invest MCP",
        "",
        f"Мандат: {rep['meta']['mandate']}. Заявок: {s['orders']}, решения совпали с ожидаемыми: {s['as_expected']} из {s['orders']}.",
        (
            f"Нарушений мандата среди заявок: {s['violations']}. Через Мандат до банка дошло: {s['violations_sent_via_mandat']}. "
            f"Напрямую банк принял {s['violations_executed_direct']} из {s['violations']}: исполнил сразу {filled}, "
            f"выставил в стакан {placed}."
        ),
        (
            f"Время ответа шлюза вместе с запросами данных у банка: медиана {s['latency_ms_p50']:.0f} мс, "
            f"максимум {s['latency_ms_max']:.0f} мс; собственная проверка Мандата - до {s['gateway_ms_max']:.1f} мс. "
            "Каждый вызов MCP песочницы занимает 0,3-1 с, шлюз делает два круга: позиции, статусы и цены параллельно, затем стакан."
        ),
        "",
        "| # | Заявка | Мандат | Почему | Напрямую в банк | мс |",
        "|---|---|---|---|---|---|",
    ]
    for r in rep["rows"]:
        d = direct.get(r["id"])
        dtxt = "-" if d is None else (d["status"] + (f" ({d['reason'][:60]})" if d.get("reason") else ""))
        sent = f", банк: {r['broker']['status']}" if r["broker"] else ""
        lines.append(
            f"| {r['id']} | {r['what']} | {r['decision']}{sent} | {', '.join(r['failed']) or '-'} | {dtxt} | {r['ms_total']:.0f} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token-file", type=Path, required=True)
    ap.add_argument("--account", help="готовый счёт песочницы; без него открывается новый и закрывается после")
    ap.add_argument("--ca-bundle", default=str(DEFAULT_CA))
    args = ap.parse_args()
    rep = run(read_token(args.token_file.expanduser()), args.account, args.ca_bundle)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "sandbox_gateway.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    (RESULTS / "sandbox_gateway.md").write_text(to_md(rep), encoding="utf-8")
    print(json.dumps(rep["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
