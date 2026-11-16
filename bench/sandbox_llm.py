"""Агент на T-lite-it-2.1 на песочнице T-Invest MCP: один ответ модели исполняется через Мандат и напрямую.

Для каждого сценария открываются два новых счёта песочницы с одинаковым стартовым портфелем: один для режима
«через Мандат», другой для «напрямую в MCP банка». Модель видит настоящий счёт и цены песочницы. Счета
закрываются после сценария. Купон в песочнице эмулируется пополнением счёта.

Запуск: .venv/bin/python -m bench.sandbox_llm --token-file ~/Downloads/<файл> [--runs 1] [--only L01,I06]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from bench.run_llm import CREDIT, SCENARIOS, SYSTEM, call_model, execute, parse_actions
from bench.sandbox_probe import DEFAULT_CA, read_token
from bench.sandbox_scenario import START_RUB, UNIVERSE, sandbox_mandate, setup_portfolio
from bench.summarize_llm import summarize, to_markdown
from mandat.brokers.tinvest_mcp import SANDBOX_URL, TInvestMcpBroker, TInvestMcpClient

RESULTS = Path(__file__).parent / "results"
MARKET_PROBE = ["SBER_TQBR", "SU26238RMFS4_TQOB"]  # сценарии покупают акции и ОФЗ: нужны торги по обоим
WAIT_SECONDS = 600


def wait_for_trading(client: TInvestMcpClient) -> None:
    """Ждать, пока биржа принимает лимитные заявки по акциям и ОФЗ: иначе прогон меряет закрытый рынок."""
    while True:
        try:
            st = client.call("invest_get_trading_statuses", {"instrumentId": MARKET_PROBE}) or {}
            flags = [s.get("limitOrderAvailableFlag") for s in st.get("tradingStatuses", [])]
            if len(flags) == len(MARKET_PROBE) and all(flags):
                return
            print(time.strftime("%H:%M"), "торги по акциям и ОФЗ не идут, жду", flush=True)
        except Exception as exc:  # noqa: BLE001 - сбой связи: подождать и спросить снова
            print(time.strftime("%H:%M"), "статус торгов недоступен:", type(exc).__name__, flush=True)
        time.sleep(WAIT_SECONDS)


def sandbox_system() -> str:
    out = SYSTEM
    for old, new in (
        ("ОФЗ 26248 (SU26248RMFS4)", "ОФЗ 26248 (SU26248RMFS3)"),
        ("каждое 10-е число докупать TMOS на 10 000 ₽", "каждое 10-е число докупать Сбербанк (SBER) на 10 000 ₽"),
    ):
        assert old in out, old
        out = out.replace(old, new)
    return out


class SandboxBroker(TInvestMcpBroker):
    """Брокер песочницы: купон зачисляется пополнением счёта (в песочнице купонов нет)."""

    def credit_cash(self, amount: float) -> None:
        value = {"currency": "RUB", "value": f"{amount:.2f}"}
        self.client.call("invest_deposit_sandbox_account", {"accountId": self.account_id, "amount": value})


class Accounts:
    def __init__(self, client: TInvestMcpClient):
        self.client, self.opened = client, []

    def new(self) -> SandboxBroker:
        acc = (self.client.call("invest_open_sandbox_account", {}) or {}).get("accountId")
        if not acc:
            raise RuntimeError("Песочница не открыла счёт")
        self.opened.append(acc)
        value = {"currency": "RUB", "value": str(START_RUB)}
        self.client.call("invest_deposit_sandbox_account", {"accountId": acc, "amount": value})
        broker = SandboxBroker(self.client, account_id=acc, universe=dict(UNIVERSE))
        setup_portfolio(broker)
        return broker

    def close_all(self) -> None:
        while self.opened:
            acc = self.opened.pop()
            try:
                self.client.call("invest_close_sandbox_account", {"accountId": acc})
            except Exception as exc:  # noqa: BLE001 - не теряем результаты из-за закрытия счёта
                print("не закрылся счёт песочницы:", type(exc).__name__, flush=True)


def run(
    token: str,
    ca: str,
    model: str,
    runs: int,
    temperature: float,
    only: set[str],
    save: Any = None,
    done: dict[str, Any] | None = None,
) -> dict[str, Any]:
    client = TInvestMcpClient(SANDBOX_URL, token=token, ca_bundle=ca)
    scenarios = [json.loads(line) for line in SCENARIOS.open(encoding="utf-8") if line.strip()]
    scenarios = [s for s in scenarios if not only or s["id"] in only]
    system, mandate = sandbox_system(), sandbox_mandate()
    records, failures = list((done or {}).get("records", [])), []
    finished = {(r["id"], r["run"]) for r in records}
    meta = {
        "model": model,
        "model_source": "t-tech/T-lite-it-2.1-GGUF, Q4_K_M",
        "runs": runs,
        "temperature": temperature,
        "scenarios": len(scenarios),
        "broker": "песочница T-Invest MCP, два новых счёта на сценарий",
    }
    for sc in scenarios:
        for run_no in range(1, runs + 1):
            if (sc["id"], run_no) in finished:  # продолжение прерванного прогона
                continue
            wait_for_trading(client)
            accounts = Accounts(client)
            try:
                via, direct = accounts.new(), accounts.new()
                market = via.snapshot()
                books = {t: market["books"].get(t) for t in market["instruments"]}
                books = {t: b for t, b in books.items() if b and b.get("asks")}  # пустой стакан (TMOS) - без цены
                prompt_market = {**market, "books": books}
                if sc["id"] in CREDIT:
                    prompt_market["cash_rub"] += CREDIT[sc["id"]]
                raw, dt = call_model(model, sc, prompt_market, seed=run_no, temperature=temperature, system=system)
                reasoning, actions, err = parse_actions(raw, prompt_market)
                rec = {
                    "id": sc["id"],
                    "class": sc["class"],
                    "run": run_no,
                    "llm_seconds": round(dt, 1),
                    "parse_error": err,
                    "reasoning": reasoning,
                    "actions": actions,
                    "raw": raw[:1500],
                    "mandat": execute(actions, "mandat", sc["id"], broker=via, mandate=mandate),
                    "direct": execute(actions, "direct", sc["id"], broker=direct, mandate=mandate),
                }
            except Exception as exc:
                if "HTTP 401" in str(exc):
                    raise SystemExit(
                        "Песочница не принимает токен (HTTP 401): прогон остановлен, продолжить --resume"
                    ) from exc
                failures.append({"id": sc["id"], "run": run_no, "error": f"{type(exc).__name__}: {exc}"[:300]})
                print(f"{sc['id']} r{run_no} СБОЙ: {failures[-1]['error'][:120]}", flush=True)
                continue
            finally:
                accounts.close_all()
            records.append(rec)
            if save:
                save({"meta": meta, "records": records, "failures": failures})
            d, m = rec["direct"], rec["mandat"]
            print(
                f"{sc['id']} r{run_no} {dt:5.1f}s actions={len(actions)} direct_viol={d['violations_reached']} "
                f"mandat_viol={m['violations_reached']} mandat={[s['decision'] for s in m['steps']]} "
                f"bank_direct={[s.get('broker', {}).get('status') if isinstance(s.get('broker'), dict) else s['decision'] for s in d['steps']]}",
                flush=True,
            )
    return {"meta": meta, "records": records, "failures": failures}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token-file", type=Path, required=True)
    ap.add_argument("--ca-bundle", default=str(DEFAULT_CA))
    ap.add_argument("--model", default="tlite21")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--only", default="", help="через запятую: id сценариев")
    ap.add_argument("--resume", action="store_true", help="продолжить: готовые записи из sandbox_llm.json не повторять")
    args = ap.parse_args()
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / "sandbox_llm.json"

    def save(data: dict[str, Any]) -> None:
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    token = read_token(args.token_file.expanduser())
    done = json.loads(out.read_text(encoding="utf-8")) if args.resume and out.exists() else None
    data = run(token, args.ca_bundle, args.model, args.runs, args.temperature, only, save=save, done=done)
    save(data)
    s = summarize(data)
    (RESULTS / "sandbox_llm_summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    md = to_markdown(s).replace("# ", "# Песочница T-Invest MCP. ", 1)
    (RESULTS / "sandbox_llm.md").write_text(md, encoding="utf-8")
    print(
        json.dumps(
            {
                k: s[k]
                for k in (
                    "attack_runs",
                    "attack_breached_direct",
                    "attack_breached_mandat",
                    "legit_runs",
                    "legit_ok_direct",
                )
            }
            | {"failures": len(data["failures"])},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
