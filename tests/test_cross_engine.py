"""Сверка Python-движка с JS-движком (docs/assets/engine.js) через bench/cross_engine.mjs."""

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bench import dump_eval_cases
from mandat.fixtures import demo_mandate, demo_start, load_market
from mandat.models import fresh_state
from mandat.rules import CHECK_CODES

ROOT = Path(__file__).resolve().parents[1]
CROSS = ROOT / "bench" / "cross_engine.mjs"
ENGINE = ROOT / "docs" / "assets" / "engine.js"
NODE = shutil.which("node")

needs_node = pytest.mark.skipif(NODE is None, reason="нужен node 18+")
needs_engine = pytest.mark.skipif(not ENGINE.exists(), reason="docs/assets/engine.js ещё не написан")


def run_node(*args):
    return subprocess.run([NODE, str(CROSS), *args], capture_output=True, text=True, timeout=180)


@pytest.fixture(scope="module")
def recorder():
    return dump_eval_cases.collect()


# --- корпус случаев ---


def test_corpus_covers_every_check_failing_and_passing(recorder):
    seen = {}
    for case in recorder.cases.values():
        for check in case["output"]["checks"]:
            seen.setdefault(check["code"], set()).add(check["ok"])
    assert set(seen) == set(CHECK_CODES)
    assert all(outcomes == {True, False} for outcomes in seen.values()), {c: o for c, o in seen.items() if len(o) < 2}


def test_corpus_has_every_decision_and_the_spec7_sequence(recorder):
    assert {c["output"]["decision"] for c in recorder.cases.values()} == {"ALLOW", "ASK", "DENY"}
    sources = [c["source"] for c in recorder.cases.values()]
    assert sources[0] == "demo" and sum(s == "demo" for s in sources) >= 40
    assert any(s.startswith("run-04:") for s in sources) and any(s.startswith("edge:") for s in sources)


def test_corpus_inputs_are_json_and_frozen_at_call_time(recorder):
    for case in recorder.cases.values():
        assert set(case["input"]) == {"order", "mandate", "market", "state"}
        assert set(case["output"]) == {"decision", "checks", "explanation", "order_value", "actions"}
        json.dumps(case)
    runaway = next(c for c in recorder.cases.values() if c["output"]["checks"][-1]["code"] == "RUNAWAY")
    assert len(runaway["input"]["state"]["recent_order_ts"]) >= 5  # состояние на момент вызова, не после него


def test_dump_writes_one_json_per_line(tmp_path):
    out = tmp_path / "cases.jsonl"
    assert dump_eval_cases.main(["--out", str(out)]) == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) > 600 and all(json.loads(line)["output"]["decision"] for line in lines)


# --- настоящий JS-движок ---


@needs_node
@needs_engine
def test_python_and_js_engines_agree(tmp_path):
    cases = tmp_path / "cases.jsonl"
    assert dump_eval_cases.main(["--out", str(cases)]) == 0
    proc = run_node("--cases", str(cases))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "расхождений: 0" in proc.stdout


@needs_node
@needs_engine
def test_committed_cases_file_agrees_with_js_engine():
    committed = ROOT / "bench" / "eval_cases.jsonl"
    assert committed.exists(), "python -m bench.dump_eval_cases"
    proc = run_node("--cases", str(committed))
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --- сам сверщик: подставные движки в разной упаковке ---


@pytest.fixture
def mini_cases(tmp_path):
    market, mandate = load_market(), demo_mandate()
    rec = dump_eval_cases.Recorder()
    now = demo_start()
    for i, (ticker, qty) in enumerate([("SBER", 10), ("SBER", 15), ("YDEX", 1)]):
        rec.source = f"mini:{i}"
        order = {
            "order_id": None,
            "ticker": ticker,
            "side": "buy",
            "quantity": qty,
            "price": None,
            "agent": "t",
            "reason": "",
        }
        rec(order, mandate, copy.deepcopy(market), fresh_state(now=now))
    path = tmp_path / "mini.jsonl"
    path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in rec.cases.values()), encoding="utf-8")
    return path


def oracle_table(cases_path):
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines()]
    return {json.dumps(c["input"]["order"], ensure_ascii=False, separators=(",", ":")): c["output"] for c in cases}


PACKAGINGS = {
    "esm-named": ("engine.js", "const T = {table};\nexport function evaluate({{ order }}) {{ return structuredClone(T[JSON.stringify(order)]); }}\n"),
    "esm-default-object": ("engine.js", "const T = {table};\nfunction evaluate({{ order }}) {{ return structuredClone(T[JSON.stringify(order)]); }}\nexport default {{ evaluate }};\n"),
    "commonjs": ("engine.cjs", "const T = {table};\nmodule.exports = {{ evaluate({{ order }}) {{ return structuredClone(T[JSON.stringify(order)]); }} }};\n"),
    "browser-global": ("engine.js", "(function (root) {{\n  const T = {table};\n  root.MandatEngine = {{ evaluate({{ order }}) {{ return structuredClone(T[JSON.stringify(order)]); }} }};\n}})(typeof window !== 'undefined' ? window : globalThis);\n"),
}  # fmt: skip


def write_engine(tmp_path, style, table, tweak=None):
    name, template = PACKAGINGS[style]
    table = copy.deepcopy(table)
    if tweak:
        tweak(table)
    path = tmp_path / name
    path.write_text(template.format(table=json.dumps(table, ensure_ascii=False)), encoding="utf-8")
    return path


@needs_node
@pytest.mark.parametrize("style", sorted(PACKAGINGS))
def test_comparer_accepts_matching_engine_in_any_packaging(tmp_path, mini_cases, style):
    engine = write_engine(tmp_path, style, oracle_table(mini_cases))
    proc = run_node("--cases", str(mini_cases), "--engine", str(engine))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Случаев: 3, совпало: 3, расхождений: 0" in proc.stdout


@needs_node
def test_comparer_reports_wrong_decision_with_the_source_tag(tmp_path, mini_cases):
    def flip(table):
        for output in table.values():
            if output["decision"] == "DENY":
                output["decision"] = "ALLOW"

    engine = write_engine(tmp_path, "esm-named", oracle_table(mini_cases), flip)
    proc = run_node("--cases", str(mini_cases), "--engine", str(engine))
    assert proc.returncode == 1
    assert "decision: python=DENY js=ALLOW" in proc.stdout and "[mini:1]" in proc.stdout
    assert "расхождений: 1" in proc.stdout


@needs_node
def test_comparer_reports_different_check_lists(tmp_path, mini_cases):
    def drop_last_check(table):
        for output in table.values():
            output["checks"].pop()

    def flip_severity(table):
        for output in table.values():
            output["checks"][-1]["severity"] = "hard" if output["checks"][-1]["severity"] == "soft" else "soft"

    for tweak in (drop_last_check, flip_severity):
        engine = write_engine(tmp_path, "esm-named", oracle_table(mini_cases), tweak)
        proc = run_node("--cases", str(mini_cases), "--engine", str(engine))
        assert proc.returncode == 1 and "checks: расходятся на позиции" in proc.stdout


@needs_node
def test_minor_differences_warn_and_fail_only_in_strict_mode(tmp_path, mini_cases):
    def reword(table):
        for output in table.values():
            output["explanation"] = "совсем другой смысл"

    engine = write_engine(tmp_path, "esm-named", oracle_table(mini_cases), reword)
    assert run_node("--cases", str(mini_cases), "--engine", str(engine)).returncode == 0
    strict = run_node("--cases", str(mini_cases), "--engine", str(engine), "--strict")
    assert strict.returncode == 1 and "explanation: 3" in strict.stdout


@needs_node
def test_comparer_reports_missing_engine_and_cases(tmp_path, mini_cases):
    missing = run_node("--cases", str(mini_cases), "--engine", str(tmp_path / "nope.js"))
    assert missing.returncode == 2 and "Нет файла движка" in missing.stderr
    broken = tmp_path / "engine.js"
    broken.write_text("export const x = 1;\n", encoding="utf-8")
    proc = run_node("--cases", str(mini_cases), "--engine", str(broken))
    assert proc.returncode == 2 and "Не удалось получить evaluate" in proc.stderr


@needs_node
def test_typeless_esm_engine_loads_on_node_without_module_detection(tmp_path, mini_cases):
    """Node 18 не угадывает ES-модуль в .js без package.json: сверщик должен загрузить его через копию .mjs."""
    flag = "--no-experimental-detect-module"
    if subprocess.run([NODE, flag, "-e", "0"], capture_output=True).returncode != 0:
        pytest.skip("эта версия node не знает флага detect-module")
    engine = write_engine(tmp_path, "esm-named", oracle_table(mini_cases))
    proc = subprocess.run(
        [NODE, flag, str(CROSS), "--cases", str(mini_cases), "--engine", str(engine)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_committed_cases_file_is_current(tmp_path):
    """bench/eval_cases.jsonl должен соответствовать текущему коду: python -m bench.dump_eval_cases."""
    committed = ROOT / "bench" / "eval_cases.jsonl"
    fresh = tmp_path / "fresh.jsonl"
    dump_eval_cases.main(["--out", str(fresh)])
    assert committed.read_text(encoding="utf-8") == fresh.read_text(encoding="utf-8")
