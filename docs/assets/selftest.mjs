// Самопроверка JS-движка на эталонной последовательности SPEC 7.
// Запуск: node assets/selftest.mjs   (код выхода 1 при любом расхождении)
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  Gateway,
  lint,
  applyFix,
  canonicalJSON,
  sha256Hex,
  sha256Fallback,
  evaluate,
  plural,
} from "./engine.js";
import { SCENARIOS, SCENARIO_ORDER, runScenario, DEMO_FIX_CODE } from "./scenarios.js";
import { MARKET as INLINE_MARKET, MANDATES as INLINE_MANDATES } from "./data.js";
import { sourcesHash } from "./build_data.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const readJSON = (rel) => JSON.parse(readFileSync(join(here, "..", "data", rel), "utf8"));
const market = readJSON("market_demo.json");
const t1 = readJSON("mandates/t1_bonds.json");
const t2 = readJSON("mandates/t2_free.json");

let failures = 0;
const problems = [];
function check(cond, msg) {
  if (!cond) {
    failures += 1;
    problems.push(msg);
  }
  return cond;
}
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// Ожидания SPEC 7: decision, полный список проваленных проверок (fails)
// или первый жёсткий провал + обязательные коды (firstHard/includes).
const EXPECT = [
  { sc: "S1", ev: "decision", order: ["SU26248RMFS4", "buy", 4, 905.6], decision: "ALLOW", fails: [] },
  { sc: "S2", ev: "decision", order: ["TMOS", "buy", 1418, null], decision: "ALLOW", fails: [] },
  { sc: "S3", ev: "decision", order: ["YDEX", "buy", 1, 4121], decision: "ASK", fails: ["WHITELIST"] },
  { sc: "S3", ev: "confirm", order: ["YDEX", "buy", 1, 4121], decision: "ALLOW", fails: [] },
  { sc: "S6", ev: "decision", order: ["SU26243RMFS4", "buy", 40, 746.4], decision: "ASK", fails: ["BOOK_DEPTH"] },
  { sc: "S7", ev: "decision", order: ["SBER", "buy", 15, 305.5], decision: "DENY", fails: ["LOT"] },
  { sc: "S8", ev: "decision", order: ["SU52002RMFS1", "buy", 1, 984.5], decision: "DENY", fails: ["CURRENCY", "WHITELIST"] },
  { sc: "S10", ev: "decision", order: ["SBER", "buy", 130, 305.5], decision: "DENY", fails: ["POSITION_SHARE", "ASK_THRESHOLD"] },
  { sc: "S9", ev: "decision", order: null, decision: "DENY", fails: ["MANDATE_CHANGE"] },
  { sc: "S4", ev: "decision", order: ["TMOS", "sell", 19000, null], decision: "DENY", firstHard: "SIDE", includes: ["SIDE"], pausedAfter: false },
  { sc: "S4", ev: "decision", order: ["SBER", "sell", 400, null], decision: "DENY", firstHard: "SIDE", includes: ["SIDE"], pausedAfter: false },
  { sc: "S4", ev: "decision", order: ["SiZ6", "buy", 10, 84510], decision: "DENY", firstHard: "CLASS", includes: ["CLASS", "FUNDS"], pausedAfter: true },
];

const failing = (checks) => checks.filter((c) => !c.ok).map((c) => c.code);
const firstHardFail = (checks) => {
  const c = checks.find((x) => !x.ok && x.severity === "hard");
  return c ? c.code : null;
};

function clock(gw, ts) {
  const d = new Date((ts + gw.tzOffset) * 1000);
  const p = (n) => String(n).padStart(2, "0");
  const frac = ts % 1 ? "." + String(Math.round((ts % 1) * 100)).padStart(2, "0") : "   ";
  return `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}${frac}`;
}

function orderText(o) {
  if (!o) return "update_mandate(limits.max_position_share=0.3)";
  return `${o.side} ${o.ticker} x${o.quantity} @ ${o.price === null ? "рынок" : o.price}`;
}

function failText(checks) {
  const f = checks.filter((c) => !c.ok);
  if (!f.length) return "-";
  return f.map((c) => (c.severity === "soft" ? "~" : "") + c.code).join(", ");
}

const rows = [];
function row(sc, ts, action, decision, checks, verdict) {
  rows.push({ sc, ts, action, decision, fails: failText(checks), verdict });
}

async function main() {
  // 0. Данные, канон JSON, SHA-256
  check(same(INLINE_MARKET, market), "data.js: MARKET не совпадает с market_demo.json (запустите build_data.mjs)");
  const bundleHead = readFileSync(join(here, "demo.bundle.js"), "utf8").slice(0, 400);
  const bundleHash = (/source-hash: ([0-9a-f]{64})/.exec(bundleHead) || [])[1];
  check(bundleHash === sourcesHash(), "demo.bundle.js устарел: запустите node assets/build_data.mjs");
  check(same(INLINE_MANDATES.t1, t1) && same(INLINE_MANDATES.t2, t2), "data.js: мандаты не совпадают с JSON (запустите build_data.mjs)");
  check(canonicalJSON({ b: 1, a: [1, "ё", null], c: { z: true, y: 0.15 } }) === '{"a":[1,"ё",null],"b":1,"c":{"y":0.15,"z":true}}', "canonicalJSON: формат");
  for (const s of ["", "abc", "Мандат - доверенность", "x".repeat(200)]) {
    const a = await sha256Hex(s);
    const b = sha256Fallback(new TextEncoder().encode(s));
    check(a === b, `sha256: запасная реализация расходится с WebCrypto на «${s.slice(0, 12)}»`);
  }
  check((await sha256Hex("abc")) === "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", "sha256('abc')");

  // 1. Стресс-тест
  const l1 = lint(t1);
  check(same(l1.map((f) => f.code), ["L_NO_DAILY"]), `lint T1: ожидали [L_NO_DAILY], получили ${l1.map((f) => f.code)}`);
  const l2 = lint(t2);
  const crit2 = l2.filter((f) => f.level === "critical").map((f) => f.code);
  check(same(crit2, ["L_NO_RUNAWAY", "L_LEVERAGE", "L_DERIVATIVES"]), `lint T2 critical: ${crit2}`);
  const gwT2 = new Gateway({ market });
  let t2Blocked = false;
  try {
    await gwT2.signMandate(t2);
  } catch {
    t2Blocked = true;
  }
  check(t2Blocked, "T2 с критичными находками не должен подписываться");
  let t2Fixed = t2;
  for (const f of lint(t2).filter((x) => x.level === "critical")) t2Fixed = applyFix(t2Fixed, f.fix);
  check(!lint(t2Fixed).some((f) => f.level === "critical"), "T2 после исправлений всё ещё critical");

  // 2. Мандат T1 с исправленным daily_turnover = 100000
  const fix = l1.find((f) => f.code === DEMO_FIX_CODE).fix;
  const mandate = applyFix(t1, fix);
  check(mandate.limits.daily_turnover === 100000, "исправление L_NO_DAILY должно ставить 100000");
  const gw = new Gateway({ market });
  const signed = await gw.signMandate(mandate);
  check(signed.hash === (await sha256Hex(canonicalJSON(mandate))), "mandate_hash = sha256(canonical_json(mandate))");

  // 3. Сценарии по порядку SPEC 7
  const expectQueue = EXPECT.slice();
  let s5 = null;
  for (const id of SCENARIO_ORDER) {
    const sc = SCENARIOS[id];
    let scStart = null;
    for await (const ev of runScenario(gw, sc, { client: true })) {
      if (ev.type === "scene") {
        scStart = ev.slot;
        continue;
      }
      if (ev.type === "resume") {
        row(id, gw.now, "клиент снимает паузу", "-", [], check(!gw.state.paused, "S5: после resume пауза должна сняться") ? "ok" : "FAIL");
        continue;
      }
      if (ev.type === "burst") {
        if (!s5) s5 = { results: [], start: gw.now };
        s5.results.push(ev.result);
        continue;
      }
      const res =
        ev.type === "coupon" ? ev.result.outcome
          : ev.type === "monthly" ? (ev.result.items[0] || {}).outcome
            : ev.result;
      const exp = expectQueue.shift();
      if (!check(!!exp, `${id}: лишнее событие ${ev.type}`)) continue;
      if (!check(!!res, `${id}: поручение не создало заявку`)) continue;
      const evKind = ev.type === "confirm" ? "confirm" : "decision";
      let ok = check(exp.sc === id && exp.ev === evKind, `${id}: ожидали ${exp.sc}/${exp.ev}, получили ${evKind}`);
      const o = res.order || null;
      const got = o ? [o.ticker, o.side, o.quantity, o.price] : null;
      ok = check(same(got, exp.order), `${id}: заявка ${JSON.stringify(got)} вместо ${JSON.stringify(exp.order)}`) && ok;
      ok = check(res.decision === exp.decision, `${id}: решение ${res.decision} вместо ${exp.decision}`) && ok;
      if (exp.fails) ok = check(same(failing(res.checks), exp.fails), `${id}: провалы [${failing(res.checks)}] вместо [${exp.fails}]`) && ok;
      if (exp.firstHard) ok = check(firstHardFail(res.checks) === exp.firstHard, `${id}: первый жёсткий провал ${firstHardFail(res.checks)} вместо ${exp.firstHard}`) && ok;
      for (const code of exp.includes || []) ok = check(failing(res.checks).includes(code), `${id}: среди провалов нет ${code}`) && ok;
      if (exp.pausedAfter !== undefined) ok = check(gw.state.paused === exp.pausedAfter, `${id}: пауза после заявки = ${gw.state.paused}, ожидали ${exp.pausedAfter}`) && ok;
      row(id, res.entry ? res.entry.ts : gw.now, (evKind === "confirm" ? "confirm: " : "") + orderText(o), res.decision, res.checks, ok ? "ok" : "FAIL");
    }
    if (id === "S1") check(scStart === gw.asOf, "S1 должен стартовать в as_of");
  }
  check(expectQueue.length === 0, `не дошли до ожиданий: ${expectQueue.map((e) => e.sc).join(", ")}`);

  // 4. S5: 1-5 ALLOW, 6-я RUNAWAY + PAUSE, 7-40 PAUSED
  check(!!s5 && s5.results.length === 40, "S5: должно быть 40 заявок");
  if (s5) {
    let ok = true;
    s5.results.forEach((r, i) => {
      const n = i + 1;
      if (n <= 5) ok = check(r.decision === "ALLOW" && failing(r.checks).length === 0, `S5 #${n}: ${r.decision} [${failing(r.checks)}] вместо ALLOW`) && ok;
      else if (n === 6) ok = check(r.decision === "DENY" && same(failing(r.checks), ["RUNAWAY"]) && r.actions.includes("PAUSE") && r.pausedNow, `S5 #6: ${r.decision} [${failing(r.checks)}] actions=${r.actions} вместо DENY RUNAWAY + PAUSE`) && ok;
      else ok = check(r.decision === "DENY" && same(failing(r.checks), ["PAUSED"]), `S5 #${n}: ${r.decision} [${failing(r.checks)}] вместо DENY PAUSED`) && ok;
    });
    const reached = s5.results.filter((r) => r.broker).length;
    ok = check(reached === 5, `S5: до брокера дошло ${reached} из 40, ожидали 5`) && ok;
    ok = check(gw.state.paused, "S5: в конце агент должен быть на паузе") && ok;
    const r1 = s5.results[0];
    const r6 = s5.results[5];
    row("S5", r1.entry.ts, "40 x " + orderText(r1.order), "5 ALLOW", [], "");
    row("S5", r6.entry.ts, "#6 " + orderText(r6.order), r6.decision, r6.checks, "");
    row("S5", s5.results[39].entry.ts, "#7-#40", "34 DENY", s5.results[39].checks, ok ? `ok, до брокера ${reached}/40` : "FAIL");
  }

  // 5. Журнал: цепочка, replay, подделка
  const j = gw.journal;
  check(j.length === 54, `журнал: ${j.length} записей, ожидали 54`);
  check((await j.verify()) === null, "журнал: verify() должен вернуть null на чистой цепочке");
  let replayed = 0;
  let matched = 0;
  for (const e of j.entries) {
    if (e.type !== "decision" && e.type !== "confirm") continue;
    replayed += 1;
    const r = await j.replay(e.seq);
    if (check(r.match, `replay #${e.seq}: не совпало (${r.stored?.decision} -> ${r.replayed?.decision})`)) matched += 1;
  }
  const lastDecision = [...j.entries].reverse().find((e) => e.type === "decision");
  lastDecision.payload.decision = lastDecision.payload.decision === "DENY" ? "ALLOW" : "DENY";
  const broken = await j.verify();
  check(broken === lastDecision.seq, `подделка #${lastDecision.seq}: verify вернул ${broken}`);
  const rTampered = await j.replay(lastDecision.seq);
  check(rTampered.match === false, "replay подделанной записи должен показать расхождение");

  // 6. Контракт evaluate() без шлюза: та же заявка S7 на исходных данных
  const direct = evaluate({
    order: { order_id: null, ticker: "SBER", side: "buy", quantity: 15, price: 305.5, agent: "x", reason: "" },
    mandate,
    market,
    state: { now: 0, turnover_today: 0, recent_order_ts: [], seen_order_ids: [], violation_ts: [], paused: false, kill_switch: false },
  });
  check(direct.decision === "DENY" && same(failing(direct.checks), ["LOT"]) && direct.checks.length === 19, `evaluate(): S7 напрямую ${direct.decision} [${failing(direct.checks)}], проверок ${direct.checks.length}`);
  check(direct.order_value === 4582.5, `evaluate(): order_value ${direct.order_value}`);

  // Печать
  const pad = (s, n) => String(s).padEnd(n);
  console.log("Мандат · selftest · market_demo.json + t1_bonds.json (daily_turnover = 100000)");
  console.log(`mandate_hash ${signed.hash}`);
  console.log("");
  console.log(pad("sc", 5) + pad("время", 13) + pad("действие", 48) + pad("решение", 9) + pad("провалы (~ мягкие)", 74) + "  итог");
  for (const r of rows) {
    console.log(pad(r.sc, 5) + pad(clock(gw, r.ts), 13) + pad(r.action, 48) + pad(r.decision, 9) + pad(r.fails, 74) + "  " + r.verdict);
  }
  console.log("");
  console.log(`lint: T1 [${l1.map((f) => f.code)}] · T2 critical [${crit2}], подпись T2 заблокирована: ${t2Blocked ? "да" : "нет"}`);
  console.log(`журнал: ${j.length} ${plural(j.length, "запись", "записи", "записей")}, verify до подделки = null, replay ${matched}/${replayed} совпали, подделка #${lastDecision.seq} -> verify = ${broken}`);
  const st = gw.stats;
  console.log(`итог шлюза: попыток ${st.attempts}, исполнено ${st.executed}, ждут ${gw.pending.size}, отклонено ${st.denied + st.rejected}, без шлюза дошло бы нарушений ${st.wouldViolate}, с шлюзом без подтверждения ${st.slipped}`);
  console.log("");
  if (failures) {
    console.log(`FAIL: ${failures}`);
    for (const p of problems) console.log("  - " + p);
    process.exit(1);
  }
  console.log("PASS: все решения и коды проверок совпали с SPEC 7");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
