#!/usr/bin/env node
// Сверка двух движков: Python (bench/eval_cases.jsonl) и JS (docs/assets/engine.js). Node 18+, без зависимостей.
//
//   node bench/cross_engine.mjs [--cases путь] [--engine путь] [--show N] [--strict]
//
// Для каждого случая вызывается evaluate({order, mandate, market, state}) и сравниваются:
//   - decision;
//   - список (code, ok, severity) в том же порядке.
// order_value, actions и (с --strict) пояснение только предупреждают; с --strict расхождение в них тоже ошибка.
// Код выхода 1 при любом расхождении.

import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import vm from "node:vm";

const here = dirname(fileURLToPath(import.meta.url));

function parseArgs(argv) {
  const opts = {
    cases: join(here, "eval_cases.jsonl"),
    engine: join(here, "..", "docs", "assets", "engine.js"),
    show: 8,
    strict: false,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--cases") opts.cases = resolve(argv[++i]);
    else if (arg === "--engine") opts.engine = resolve(argv[++i]);
    else if (arg === "--show") opts.show = Number(argv[++i]);
    else if (arg === "--strict") opts.strict = true;
    else throw new Error(`Неизвестный аргумент: ${arg}`);
  }
  return opts;
}

// --- загрузка движка: ES-модуль, CommonJS или обычный скрипт с глобальным объектом ---

function pickEvaluate(mod) {
  if (!mod) return null;
  if (typeof mod.evaluate === "function") return mod.evaluate;
  const fallback = mod.default;
  if (fallback && typeof fallback.evaluate === "function") return fallback.evaluate.bind(fallback);
  if (typeof fallback === "function" && fallback.name === "evaluate") return fallback;
  return null;
}

function scanGlobals(sandbox) {
  if (typeof sandbox.evaluate === "function") return sandbox.evaluate;
  for (const key of Object.keys(sandbox)) {
    const value = sandbox[key];
    if (value && (typeof value === "object" || typeof value === "function") && typeof value.evaluate === "function") {
      return value.evaluate.bind(value);
    }
  }
  return null;
}

async function loadEvaluate(file) {
  const notes = [];
  try {
    const found = pickEvaluate(await import(pathToFileURL(file).href));
    if (found) return found;
    notes.push("модуль загружен, но экспорта evaluate нет");
  } catch (error) {
    notes.push(String(error.message).split("\n")[0]);
  }
  const source = readFileSync(file, "utf8");
  if (/^\s*(export|import)\s/m.test(source)) {
    // файл .js без "type": "module" на Node 18: читаем как .mjs
    const dir = mkdtempSync(join(tmpdir(), "mandat-engine-"));
    try {
      const copy = join(dir, "engine.mjs");
      writeFileSync(copy, source);
      const found = pickEvaluate(await import(pathToFileURL(copy).href));
      if (found) return found;
    } catch (error) {
      notes.push(String(error.message).split("\n")[0]);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  } else {
    // обычный скрипт: даём ему module/exports, window и веб-глобалы, которых нет в голом vm-контексте
    const web = { console, structuredClone, TextEncoder, TextDecoder, URL, URLSearchParams, setTimeout, clearTimeout };
    const sandbox = { ...web, crypto: globalThis.crypto, module: { exports: {} }, exports: {} };
    sandbox.window = sandbox.self = sandbox.globalThis = sandbox;
    try {
      vm.runInNewContext(source, vm.createContext(sandbox), { filename: file });
      const found = pickEvaluate(sandbox.module.exports) || pickEvaluate(sandbox.exports) || scanGlobals(sandbox);
      if (found) return found;
    } catch (error) {
      notes.push(String(error.message).split("\n")[0]);
    }
  }
  throw new Error(`Не удалось получить evaluate из ${file}: ${notes.join("; ")}`);
}

// --- сравнение ---

const signature = (checks) => (checks || []).map((c) => `${c.code}|${c.ok}|${c.severity}`);
const describeCheck = (c) => (c ? `${c.code} ok=${c.ok} ${c.severity}` : "(нет)");
const describeOrder = (o) =>
  `${o.side} ${o.ticker} x${o.quantity} @ ${o.price === null || o.price === undefined ? "рынок" : o.price}`;
// пояснения могут отличаться форматом чисел: сравниваем без цифр, пробелов и знаков
const wording = (text) => String(text || "").replace(/[\d\s.,%  ₽-]/g, "");

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (!existsSync(opts.engine)) throw new Error(`Нет файла движка: ${opts.engine}`);
  if (!existsSync(opts.cases)) throw new Error(`Нет файла случаев: ${opts.cases} (python -m bench.dump_eval_cases)`);
  const evaluate = await loadEvaluate(opts.engine);
  const lines = readFileSync(opts.cases, "utf8").split("\n").filter((line) => line.trim());

  let mismatched = 0;
  const soft = { order_value: 0, actions: 0, explanation: 0, mutated_input: 0 };
  const shown = [];
  for (let index = 0; index < lines.length; index += 1) {
    const { source, input, output } = JSON.parse(lines[index]);
    const original = JSON.stringify(input);
    const copy = JSON.parse(original);
    const problems = [];
    let got = null;
    try {
      got = (await evaluate(copy)) ?? null;
    } catch (error) {
      problems.push(`JS-движок упал: ${error.message}`);
    }
    if (got !== null && (typeof got !== "object" || Array.isArray(got))) {
      problems.push(`JS-движок вернул не объект: ${JSON.stringify(got)}`);
    } else if (got === null && problems.length === 0) {
      problems.push("JS-движок ничего не вернул");
    }
    if (got && typeof got === "object") {
      if (got.decision !== output.decision) problems.push(`decision: python=${output.decision} js=${got.decision}`);
      const want = signature(output.checks);
      const have = signature(got.checks);
      if (want.join(",") !== have.join(",")) {
        let at = 0;
        while (at < want.length && want[at] === have[at]) at += 1;
        problems.push(
          `checks: расходятся на позиции ${at + 1}: python=${describeCheck(output.checks[at])}, ` +
            `js=${describeCheck((got.checks || [])[at])} (всего ${want.length} и ${have.length})`,
        );
      }
      if (Math.abs((got.order_value ?? NaN) - output.order_value) > 0.0101) soft.order_value += 1;
      if (JSON.stringify(got.actions || []) !== JSON.stringify(output.actions || [])) soft.actions += 1;
      if (wording(got.explanation) !== wording(output.explanation)) soft.explanation += 1;
      if (JSON.stringify(copy) !== original) soft.mutated_input += 1;
    }
    const strictFail = opts.strict && (soft.order_value || soft.actions || soft.explanation || soft.mutated_input);
    if (problems.length) {
      mismatched += 1;
      if (shown.length < opts.show) shown.push({ line: index + 1, source, order: input.order, problems });
    } else if (strictFail && shown.length < opts.show) {
      shown.push({ line: index + 1, source, order: input.order, problems: ["мелкие расхождения (см. счётчики ниже)"] });
    }
  }

  for (const item of shown) {
    console.log(`\n#${item.line} [${item.source ?? "?"}] ${describeOrder(item.order)}`);
    for (const problem of item.problems) console.log(`   ${problem}`);
  }
  console.log(`\nСлучаев: ${lines.length}, совпало: ${lines.length - mismatched}, расхождений: ${mismatched}`);
  const notes = Object.entries(soft).filter(([, n]) => n > 0);
  if (notes.length) {
    console.log(`Предупреждения (${opts.strict ? "ошибки" : "не влияют на код выхода"}): ` +
      notes.map(([name, n]) => `${name}: ${n}`).join(", "));
  }
  const strictBad = opts.strict && notes.length > 0;
  process.exit(mismatched > 0 || strictBad ? 1 : 0);
}

main().catch((error) => {
  console.error(`Ошибка: ${error.message}`);
  process.exit(2);
});
