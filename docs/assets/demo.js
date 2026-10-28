// Мандат - интерактивный прототип: панели «Мандат», «Агент работает», «Журнал».
import { MARKET, MANDATES } from "./data.js";
import {
  Gateway,
  lint,
  applyFix,
  describeMandate,
  canonicalJSON,
  getPath,
  clone,
  fmtMoney,
  fmtNumber,
  fmtPct,
  plural,
  CHECK_LABELS,
} from "./engine.js";
import {
  SCENARIOS,
  SCENARIO_ORDER,
  runScenario,
  DEMO_FIX_CODE,
} from "./scenarios.js";

// ------------------------------------------------------------ константы

const TEMPLATES = {
  t1: { tag: "T1", title: "Облигации, фонд и реинвест", mandate: MANDATES.t1 },
  t2: { tag: "T2", title: "Свободный мандат", mandate: MANDATES.t2 },
};

const CLASS_ONE = {
  share: "акция",
  bond: "облигация",
  etf: "фонд",
  futures: "фьючерс",
};
const MONTHS = [
  "января",
  "февраля",
  "марта",
  "апреля",
  "мая",
  "июня",
  "июля",
  "августа",
  "сентября",
  "октября",
  "ноября",
  "декабря",
];
const TYPE_RU = {
  mandate_signed: "Подпись мандата",
  decision: "Решение шлюза",
  confirm: "Подтверждение",
  reject: "Отказ клиента",
  kill: "Выключатель",
  resume: "Снятие паузы",
};
const BADGES = {
  ALLOW: ["badge-allow", "Исполнено"],
  ASK: ["badge-ask", "Ждёт вас"],
  DENY: ["badge-deny", "Отклонено"],
  REJECTED: ["badge-neutral", "Отклонено вами"],
};

const ICONS = {
  check: '<path d="M5 12.5l4.2 4.2L19 7"/>',
  x: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
  ask: '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.4a2.5 2.5 0 0 1 4.8 1c0 1.7-2.4 2.2-2.4 3.6"/><path d="M12 17.3v.01"/>',
  ban: '<circle cx="12" cy="12" r="9"/><path d="M5.7 5.7l12.6 12.6"/>',
  can: '<path d="M5 12.5l4.2 4.2L19 7"/>',
  shield:
    '<path d="M12 3l7 3v5.5c0 4.4-3 8.2-7 9.5-4-1.3-7-5.1-7-9.5V6l7-3z"/><path d="M8.8 12.2l2.2 2.2 4.4-4.6"/>',
  play: '<path d="M8 5.8v12.4a.8.8 0 0 0 1.2.7l9.6-6.2a.8.8 0 0 0 0-1.4L9.2 5.1A.8.8 0 0 0 8 5.8z" fill="currentColor"/>',
  stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2.2" fill="currentColor"/>',
  power: '<path d="M12 3.5v8"/><path d="M6.6 7.3a7.6 7.6 0 1 0 10.8 0"/>',
  reset: '<path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3"/><path d="M4.5 4.5v4h4"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.8v2.1M12 19.1v2.1M4.9 4.9l1.5 1.5M17.6 17.6l1.5 1.5M2.8 12h2.1M19.1 12h2.1M4.9 19.1l1.5-1.5M17.6 6.4l1.5-1.5"/>',
  moon: '<path d="M19.5 14.6A8 8 0 0 1 9.4 4.5a8 8 0 1 0 10.1 10.1z"/>',
  bell: '<path d="M6.5 16.5V11a5.5 5.5 0 0 1 11 0v5.5l1.6 2H4.9l1.6-2z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
  bot: '<rect x="4.5" y="8" width="15" height="11" rx="3"/><path d="M12 4.5V8M9.3 13v1M14.7 13v1"/>',
  coin: '<circle cx="12" cy="12" r="8.5"/><path d="M10 16.5V7.5h3.2a2.4 2.4 0 0 1 0 4.8H8.5M8.5 14.4h4.6"/>',
  calendar:
    '<rect x="4" y="5.5" width="16" height="14.5" rx="2.5"/><path d="M8 3.5v4M16 3.5v4M4 10h16"/>',
  news: '<rect x="4" y="5" width="16" height="14" rx="2"/><path d="M8 9h8M8 12.5h8M8 16h5"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  replay:
    '<path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3"/><path d="M4.5 4.5v4h4"/><path d="M10.5 9.3v5.4l4.2-2.7z" fill="currentColor"/>',
  alert:
    '<path d="M12 4.2l8.5 15H3.5l8.5-15z"/><path d="M12 10v4M12 16.8v.01"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.8v.01"/>',
  dot: '<circle cx="12" cy="12" r="3.4" fill="currentColor" stroke="none"/>',
  lock: '<rect x="5" y="10.5" width="14" height="9.5" rx="2.2"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16v4z"/><path d="M13.5 6.5l4 4"/>',
  loop: '<path d="M17 4.5l2.5 2.5L17 9.5"/><path d="M4.5 11.5V10a3 3 0 0 1 3-3h12"/><path d="M7 19.5L4.5 17 7 14.5"/><path d="M19.5 12.5V14a3 3 0 0 1-3 3h-12"/>',
  user: '<circle cx="12" cy="8.5" r="3.6"/><path d="M5 20a7 7 0 0 1 14 0"/>',
};

// ------------------------------------------------------------ состояние

const ABORT = Symbol("abort");
const reduceMotion = window.matchMedia
  ? window.matchMedia("(prefers-reduced-motion: reduce)")
  : { matches: false };
const narrow = window.matchMedia
  ? window.matchMedia("(max-width: 1179px)")
  : { matches: false };
let runToken = 0;

const S = {
  gw: null,
  draft: null,
  ownText: "",
  signed: null,
  signing: false,
  running: false,
  busy: false,
  activeScenario: null,
  done: new Set(),
  cardsByPending: new Map(),
  journalRendered: 0,
  journalItems: new Map(),
  openSeq: null,
  tamperedSeq: null,
  brokenSeq: null,
  posItems: new Map(),
  lastQty: null,
  lastCash: null,
};

// ------------------------------------------------------------ утилиты DOM

const $ = (id) => document.getElementById(id);

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k === "style") el.style.cssText = v;
      else if (k.startsWith("on") && typeof v === "function")
        el.addEventListener(k.slice(2), v);
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, String(v));
    }
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : String(kid));
  }
  return el;
}

function icon(name, cls = "") {
  const span = document.createElement("span");
  span.className = "ic" + (cls ? " " + cls : "");
  span.setAttribute("aria-hidden", "true");
  span.innerHTML =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    (ICONS[name] || "") +
    "</svg>";
  return span;
}

function setButton(btn, iconName, label) {
  btn.replaceChildren(...(iconName ? [icon(iconName)] : []), label);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function pace(ms, token) {
  await sleep(ms);
  if (token !== runToken) throw ABORT;
}

function smooth() {
  return reduceMotion.matches ? "auto" : "smooth";
}

function autopress(el) {
  if (!el) return;
  el.classList.remove("is-autopress");
  void el.offsetWidth;
  el.classList.add("is-autopress");
  setTimeout(() => el.classList.remove("is-autopress"), 800);
}

function esc(s) {
  return String(s).replace(
    /[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c],
  );
}

// ------------------------------------------------------------ форматирование

function clock(ts, withMs = false) {
  const d = new Date((ts + S.gw.tzOffset) * 1000);
  const p2 = (n) => String(n).padStart(2, "0");
  let s = `${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}:${p2(d.getUTCSeconds())}`;
  const ms = Math.round((ts % 1) * 1000);
  if (withMs && ms) s += "." + String(ms).padStart(3, "0");
  return s;
}

function dateRu(ts) {
  const d = new Date((ts + S.gw.tzOffset) * 1000);
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

function displayName(ticker, inst) {
  if (!inst) return ticker;
  return inst.name.length <= 18 ? inst.name : ticker;
}

function shortHash(hash) {
  return `${hash.slice(0, 8)}…${hash.slice(-4)}`;
}

function failing(checks) {
  return (checks || []).filter((c) => !c.ok);
}

function changeText(path, value) {
  if (path === "limits.max_position_share") {
    const old = S.gw.mandate ? getPath(S.gw.mandate, path) : null;
    return `лимит доли ${old !== null && old !== undefined ? fmtPct(old, 0) + "% → " : ""}${fmtPct(value, 0)}%`;
  }
  return `${path} = ${JSON.stringify(value)}`;
}

function describeAttempt(r) {
  if (r.kind === "update_mandate") {
    const parts = Object.entries(r.changes || {}).map(([p, v]) =>
      changeText(p, v),
    );
    return {
      title: `Изменить мандат: ${parts.join(", ")}`,
      sub: "Вызов update_mandate через шлюз",
      value: null,
      reason: r.request ? r.request.reason : "",
    };
  }
  const o = r.order;
  const inst = r.snapshot ? r.snapshot.instrument : null;
  const verb = o.side === "sell" ? "Продать" : "Купить";
  const name = displayName(o.ticker, inst);
  const cur =
    inst && inst.currency !== "rub" ? inst.currency.toUpperCase() : "₽";
  const price =
    o.price === null ? "по рынку" : `по ${fmtNumber(o.price, 4)}\u00a0${cur}`;
  const sub = inst
    ? [
        inst.name !== name ? inst.name : null,
        CLASS_ONE[inst.class] || inst.class,
        o.ticker !== name ? o.ticker : null,
        inst.currency !== "rub"
          ? `курс ${fmtNumber(r.snapshot.fx, 2)} ₽`
          : null,
      ]
        .filter(Boolean)
        .join(" · ")
    : "Бумага не найдена у брокера";
  return {
    title: `${verb} ${name} ×\u00a0${fmtNumber(o.quantity, 0)} ${price}`,
    sub,
    value: inst ? fmtMoney(r.order_value) : null,
    reason: o.reason,
  };
}

function badge(decision, text) {
  const [cls, label] = BADGES[decision] || ["badge-neutral", decision];
  return h(
    "span",
    { class: `badge ${cls}` },
    decision !== "REJECTED" ? h("code", null, decision) : null,
    text || label,
  );
}

// ------------------------------------------------------------ тема

function currentTheme() {
  const t = document.documentElement.getAttribute("data-theme");
  if (t === "light" || t === "dark") return t;
  return window.matchMedia &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function setupTheme() {
  const btn = $("theme-toggle");
  const paint = () => {
    const dark = currentTheme() === "dark";
    btn.replaceChildren(icon(dark ? "sun" : "moon"));
    btn.setAttribute(
      "aria-label",
      dark ? "Включить светлую тему" : "Включить тёмную тему",
    );
    btn.title = dark ? "Светлая тема" : "Тёмная тема";
  };
  paint();
  btn.addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem("mandat-theme", next);
    } catch (e) {
      /* хранилище недоступно - тема просто не запомнится */
    }
    paint();
  });
  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    if (mq.addEventListener) mq.addEventListener("change", paint);
  }
}

// ------------------------------------------------------------ уведомления

function toast({ tone = "neutral", ic = "bell", title, text, ttl = 6500 }) {
  const box = $("toasts");
  const el = h(
    "div",
    { class: "toast", "data-tone": tone },
    h("span", { class: "toast-icon" }, icon(ic)),
    h(
      "div",
      null,
      h(
        "div",
        { class: "toast-app" },
        h("b", null, "Мандат"),
        h("span", null, `· ${clock(S.gw.now)}`),
      ),
      h("p", { class: "toast-title" }, title),
      text ? h("p", { class: "toast-text" }, text) : null,
    ),
    h(
      "button",
      {
        type: "button",
        class: "toast-close",
        "aria-label": "Закрыть уведомление",
        onclick: () => dismiss(el),
      },
      icon("x"),
    ),
  );
  box.append(el);
  while (box.children.length > 2) box.firstElementChild.remove();
  setTimeout(() => dismiss(el), ttl);
}

function dismiss(el) {
  if (!el.isConnected || el.classList.contains("is-out")) return;
  el.classList.add("is-out");
  setTimeout(() => el.remove(), reduceMotion.matches ? 0 : 240);
}

function notify(r) {
  if (r.pausedNow) {
    const runaway = r.pauseReason === "runaway";
    toast({
      tone: "deny",
      ic: "power",
      title: "Агент поставлен на паузу",
      text: runaway
        ? "Похоже на зацикливание. Новые заявки не дойдут до брокера, пока вы не снимете паузу."
        : "Три отказа за 10 минут. Новые заявки не дойдут до брокера, пока вы не снимете паузу.",
    });
    return;
  }
  if (r.decision === "DENY") {
    const first = r.checks.find((c) => !c.ok && c.severity === "hard");
    if (first && (first.code === "PAUSED" || first.code === "KILL")) return;
    toast({
      tone: "deny",
      ic: "ban",
      title: "Шлюз заблокировал действие агента",
      text: first ? first.detail : r.explanation,
    });
  } else if (r.decision === "ASK") {
    const soft = r.checks.find((c) => !c.ok && c.severity === "soft");
    toast({
      tone: "ask",
      ic: "ask",
      title: "Агент ждёт вашего решения",
      text: `${describeAttempt(r).title}. ${soft ? soft.detail : ""}`,
    });
  }
}

// ------------------------------------------------------------ контекст: время и портфель

function renderClock() {
  const time = clock(S.gw.now);
  const date = dateRu(S.gw.now);
  $("sim-clock").textContent = time;
  $("sim-date").textContent = date;
  const hdr = $("hdr-time");
  if (hdr.textContent !== time) {
    hdr.textContent = time;
    hdr.classList.remove("is-tick");
    void hdr.offsetWidth;
    hdr.classList.add("is-tick");
  }
  $("hdr-date").textContent = date;
}

function activeLimit() {
  const m = S.gw.mandate || (S.draft && S.draft.mandate);
  const v = m && m.limits ? m.limits.max_position_share : null;
  return v === null || v === undefined ? null : v;
}

function renderPortfolio() {
  const pf = S.gw.getPortfolio();
  const total = $("pf-total");
  const cash = $("pf-cash");
  total.textContent = fmtMoney(Math.round(pf.total_value));
  cash.textContent = fmtMoney(pf.cash_rub);
  if (S.lastCash !== null && S.lastCash !== pf.cash_rub) {
    cash.classList.remove("ctx-flash");
    void cash.offsetWidth;
    cash.classList.add("ctx-flash");
  }
  S.lastCash = pf.cash_rub;
  $("pf-cash-share").textContent =
    `${fmtPct(pf.cash_rub / pf.total_value)}% портфеля`;
  $("pf-turnover").textContent =
    `оборот за день ${fmtMoney(S.gw.state.turnover_today)}`;
  const limit = activeLimit();
  $("pf-legend").textContent = limit
    ? `Доли бумаг · полоса заполнена до лимита ${fmtPct(limit, 0)}%`
    : "Доли бумаг";
  const ul = $("pf-positions");
  const seen = new Set();
  for (const row of pf.positions) {
    seen.add(row.ticker);
    let item = S.posItems.get(row.ticker);
    if (!item) {
      const ticker = h("span", { class: "pos-ticker" });
      const share = h("span", { class: "pos-share" });
      const fill = h("span", { style: "width: 0%" });
      const li = h(
        "li",
        { class: "pos" },
        h("div", { class: "pos-top" }, ticker, share),
        h("div", { class: "pos-bar" }, fill),
      );
      item = { li, ticker, share, fill };
      S.posItems.set(row.ticker, item);
      ul.append(li);
    }
    const inst = MARKET.instruments[row.ticker];
    const scale = limit || 0.2;
    const near = limit && row.share >= limit * 0.9 && row.share <= limit;
    const over = limit && row.share > limit;
    item.li.className =
      "pos" + (near ? " is-near" : "") + (over ? " is-over" : "");
    item.li.title = `${row.name}: ${fmtNumber(row.qty, 0)} шт. на ${fmtMoney(row.value)}`;
    item.ticker.textContent = displayName(row.ticker, inst);
    item.share.textContent = `${fmtPct(row.share)}%`;
    requestAnimationFrame(() => {
      item.fill.style.width = `${Math.min(row.share / scale, 1) * 100}%`;
    });
    if (S.lastQty && S.lastQty[row.ticker] !== row.qty) {
      item.li.classList.remove("is-flash");
      void item.li.offsetWidth;
      item.li.classList.add("is-flash");
    }
  }
  for (const [t, item] of S.posItems) {
    if (!seen.has(t)) {
      item.li.remove();
      S.posItems.delete(t);
    }
  }
  S.lastQty = Object.fromEntries(pf.positions.map((r) => [r.ticker, r.qty]));
}

// ------------------------------------------------------------ 1. мандат

function selectTemplate(key, { keepText = false } = {}) {
  const ta = $("client-text");
  S.draft = {
    key,
    mandate: null,
    compiled: false,
    changed: new Set(),
    fixed: [],
    prevRules: null,
  };
  for (const b of document.querySelectorAll("#tpl-choice .choice-btn")) {
    const on = b.dataset.key === key;
    b.setAttribute("aria-checked", String(on));
    b.tabIndex = on ? 0 : -1;
  }
  if (key === "own") {
    if (!keepText) ta.value = S.ownText;
  } else {
    ta.value = TEMPLATES[key].mandate.client_text;
  }
  fitTextarea();
  $("compiled").hidden = true;
  $("own-note").hidden = true;
  $("compile-hint").hidden = false;
  renderSeal();
  renderMandateStatus();
}

// Поле подстраивается под текст шаблона, но не выше 220px.
function fitTextarea() {
  const ta = $("client-text");
  ta.style.height = "auto";
  ta.style.height = `${Math.min(Math.max(ta.scrollHeight + 2, 96), 220)}px`;
}

function onTextInput() {
  const ta = $("client-text");
  fitTextarea();
  S.ownText = ta.value;
  if (S.draft.key !== "own") {
    if (ta.value !== TEMPLATES[S.draft.key].mandate.client_text)
      selectTemplate("own", { keepText: true });
  } else {
    $("own-note").hidden = true;
  }
}

function compile() {
  $("compile-hint").hidden = true;
  if (S.draft.key === "own") {
    renderOwnNote();
    return;
  }
  const d = S.draft;
  d.mandate = clone(TEMPLATES[d.key].mandate);
  d.compiled = true;
  d.changed = new Set();
  d.fixed = [];
  d.prevRules = null;
  $("compiled").hidden = false;
  $("json-view").open = false;
  renderCompiled();
  renderMandateStatus();
  renderPortfolio();
}

function renderOwnNote() {
  const note = $("own-note");
  note.hidden = false;
  note.replaceChildren(
    icon("info"),
    h(
      "div",
      null,
      h(
        "p",
        null,
        h("b", null, "Свой текст в прототипе не компилируется."),
        " Компилятор здесь работает только на двух шаблонах, и мы не притворяемся, что понимаем любые слова.",
      ),
      h(
        "p",
        null,
        "В MVP текст клиента разбирает языковая модель T-Pro или GigaChat в ту же схему mandat/v1. Дальше всё как здесь: стресс-тест, исправления, подпись. Решения по заявкам и тогда принимает код, а не модель.",
      ),
      h(
        "div",
        { class: "row" },
        h(
          "button",
          {
            type: "button",
            class: "btn btn-secondary btn-sm",
            onclick: () => pickAndCompile("t1"),
          },
          "Открыть шаблон T1",
        ),
        h(
          "button",
          {
            type: "button",
            class: "btn btn-ghost btn-sm",
            onclick: () => pickAndCompile("t2"),
          },
          "Открыть шаблон T2",
        ),
      ),
    ),
  );
}

function pickAndCompile(key) {
  selectTemplate(key);
  compile();
}

function renderCompiled({ fix = null } = {}) {
  const d = S.draft;
  const m = d.mandate;
  const rules = describeMandate(m, MARKET);
  const prev = d.prevRules;
  const isNew = (group, line) => fix && prev && !prev[group].includes(line);
  const group = (key, cls, ic, title, lines, empty) =>
    h(
      "div",
      { class: `rules-group ${cls}` },
      h("h4", null, icon(ic), title),
      h(
        "ul",
        null,
        lines.length
          ? lines.map((line) =>
              h("li", { class: isNew(key, line) ? "is-new" : null }, line),
            )
          : h("li", { class: "rules-empty" }, empty),
      ),
    );
  $("rules").replaceChildren(
    group("can", "rules-can", "can", "Агент сможет", rules.can, "Ничего"),
    group(
      "cannot",
      "rules-cannot",
      "ban",
      "Не сможет",
      rules.cannot,
      "Ограничений нет",
    ),
    group(
      "ask",
      "rules-ask",
      "ask",
      "Спросит вас",
      rules.ask,
      "Ни о чём: агент действует сам",
    ),
  );
  d.prevRules = rules;
  $("json-pre").innerHTML = jsonHTML(m, d.changed);
  renderFindings();
  renderSignRow();
  renderSeal();
}

function jsonHTML(value, changed) {
  const lines = [];
  const pad = (n) => "  ".repeat(n);
  const prim = (x) => {
    if (x === null) return '<span class="jv-lit">null</span>';
    if (typeof x === "boolean") return `<span class="jv-lit">${x}</span>`;
    if (typeof x === "number") return `<span class="jv-num">${x}</span>`;
    return `<span class="jv-str">${esc(JSON.stringify(x))}</span>`;
  };
  const isPrim = (x) => x === null || typeof x !== "object";
  const walk = (x, path, depth, key, comma) => {
    const prefix =
      pad(depth) +
      (key !== null ? `<span class="jv-key">"${esc(key)}"</span>: ` : "");
    if (Array.isArray(x) && x.every(isPrim)) {
      lines.push({
        path,
        html: prefix + "[" + x.map(prim).join(", ") + "]" + comma,
      });
    } else if (Array.isArray(x)) {
      lines.push({ path, html: prefix + "[" });
      x.forEach((y, i) =>
        walk(y, `${path}.${i}`, depth + 1, null, i < x.length - 1 ? "," : ""),
      );
      lines.push({ path, html: pad(depth) + "]" + comma });
    } else if (x && typeof x === "object") {
      const keys = Object.keys(x);
      lines.push({ path, html: prefix + "{" });
      keys.forEach((k, i) =>
        walk(
          x[k],
          path ? `${path}.${k}` : k,
          depth + 1,
          k,
          i < keys.length - 1 ? "," : "",
        ),
      );
      lines.push({ path, html: pad(depth) + "}" + comma });
    } else {
      lines.push({ path, html: prefix + prim(x) + comma });
    }
  };
  walk(value, "", 0, null, "");
  return lines
    .map(
      (l) =>
        `<span class="jv-line${changed.has(l.path) ? " is-changed" : ""}">${l.html}</span>`,
    )
    .join("");
}

function renderFindings() {
  const list = $("findings");
  const findings = lint(S.draft.mandate);
  const items = [];
  for (const f of S.draft.fixed) {
    items.push(
      h(
        "li",
        { class: "finding is-fixed" },
        icon("check"),
        h(
          "div",
          { class: "finding-body" },
          h(
            "p",
            { class: "finding-text" },
            `Исправлено: ${f.fix.label.charAt(0).toLowerCase()}${f.fix.label.slice(1)}.`,
          ),
          h(
            "div",
            { class: "finding-meta" },
            h("span", { class: "finding-level" }, "Закрыто"),
            h("code", null, f.code),
          ),
        ),
      ),
    );
  }
  for (const f of findings) {
    const critical = f.level === "critical";
    items.push(
      h(
        "li",
        { class: `finding ${critical ? "is-critical" : "is-warn"}` },
        icon("alert"),
        h(
          "div",
          { class: "finding-body" },
          h("p", { class: "finding-text" }, f.text),
          h(
            "div",
            { class: "finding-meta" },
            h(
              "span",
              { class: "finding-level" },
              critical ? "Критично: подпись заблокирована" : "Внимание",
            ),
            h("code", null, f.code),
            f.fix
              ? h(
                  "span",
                  { class: "row" },
                  h(
                    "button",
                    {
                      type: "button",
                      class: "btn btn-secondary btn-sm",
                      "data-fix": f.code,
                      onclick: () => fixFinding(f.code),
                    },
                    "Исправить",
                  ),
                  h("span", { class: "hint" }, f.fix.label),
                )
              : null,
          ),
        ),
      ),
    );
  }
  if (!findings.length) {
    items.push(
      h(
        "li",
        { class: "finding is-clear" },
        icon("check"),
        h(
          "div",
          { class: "finding-body" },
          h(
            "p",
            { class: "finding-text" },
            "Дыр по таблице стресс-теста нет. Смысловые дыры в MVP ищет модель.",
          ),
        ),
      ),
    );
  }
  list.replaceChildren(...items);
}

function fixFinding(code) {
  const f = lint(S.draft.mandate).find((x) => x.code === code);
  if (!f || !f.fix) return;
  S.draft.mandate = applyFix(S.draft.mandate, f.fix);
  for (const p of Object.keys(f.fix.set)) S.draft.changed.add(p);
  S.draft.fixed.push(f);
  renderCompiled({ fix: f });
  renderMandateStatus();
  renderPortfolio();
}

function draftIsSigned() {
  return !!(
    S.signed &&
    S.draft.compiled &&
    canonicalJSON(S.draft.mandate) === S.signed.canonical
  );
}

function renderSignRow() {
  const btn = $("btn-sign");
  const hint = $("sign-hint");
  if (!S.draft.compiled) return;
  const findings = lint(S.draft.mandate);
  const crit = findings.filter((f) => f.level === "critical").length;
  const warn = findings.length - crit;
  const same = draftIsSigned();
  setButton(
    btn,
    "lock",
    S.signed && !same ? "Подписать новую версию" : "Подписать мандат",
  );
  btn.disabled = crit > 0 || same || S.signing || S.busy;
  btn.hidden = same;
  if (crit) hint.textContent = `Сначала закройте критичное: ${crit}.`;
  else if (same) hint.textContent = "Подписан: шлюз работает по этим правилам.";
  else if (S.busy) hint.textContent = "Дождитесь конца сценария.";
  else if (warn)
    hint.textContent = `${warn} ${plural(warn, "предупреждение", "предупреждения", "предупреждений")}: подписать можно, но лучше исправить.`;
  else hint.textContent = "Хеш правил станет записью в журнале.";
}

async function sign() {
  if (S.signing || !S.draft.compiled) return;
  S.signing = true;
  renderSignRow();
  try {
    const m = clone(S.draft.mandate);
    const res = await S.gw.signMandate(m);
    S.signed = {
      hash: res.hash,
      title: m.title,
      ts: res.entry.ts,
      seq: res.entry.seq,
      canonical: canonicalJSON(m),
    };
    addSys(
      `Вы подписали мандат «${m.title}»: шлюз работает по этим правилам.`,
      "ok",
      "shield",
    );
  } catch (e) {
    toast({
      tone: "deny",
      ic: "lock",
      title: "Подписать нельзя",
      text: e.message,
    });
  } finally {
    S.signing = false;
  }
  renderAll();
  if (S.signed)
    $("seal").scrollIntoView({ block: "nearest", behavior: smooth() });
}

function renderSeal() {
  const seal = $("seal");
  if (!S.signed) {
    seal.hidden = true;
    return;
  }
  const stale = S.draft.compiled ? !draftIsSigned() : S.draft.key !== "own";
  seal.hidden = false;
  seal.classList.toggle("is-stale", stale);
  seal.replaceChildren(
    h("span", { class: "seal-icon" }, icon("shield")),
    h(
      "div",
      null,
      h(
        "p",
        { class: "seal-title" },
        stale ? "Действует подписанная версия" : "Мандат подписан",
      ),
      h(
        "p",
        { class: "seal-sub" },
        `«${S.signed.title}» · ${clock(S.signed.ts)} · запись #${S.signed.seq} журнала`,
      ),
    ),
    h(
      "div",
      { class: "seal-hash" },
      h("span", null, "SHA-256"),
      h(
        "span",
        {
          class: "hash",
          tabindex: "0",
          "data-full": S.signed.hash,
          "aria-label": `Хеш мандата ${S.signed.hash}`,
        },
        shortHash(S.signed.hash),
      ),
      h(
        "button",
        {
          type: "button",
          class: "link-btn",
          onclick: () => focusJournal(S.signed.seq),
        },
        "в журнале",
      ),
    ),
  );
}

function renderMandateStatus() {
  const el = $("mandate-status");
  let tone = "neutral";
  let text = "Черновик";
  if (S.signed && draftIsSigned()) {
    tone = "ok";
    text = "Подписан";
  } else if (S.signed) {
    tone = "ask";
    text = "Есть новая версия";
  } else if (S.draft.compiled) {
    text = "Не подписан";
  }
  el.dataset.tone = tone;
  el.textContent = text;
}

// ------------------------------------------------------------ 2. агент

function buildScenarioButtons() {
  const ul = $("scenarios");
  ul.replaceChildren(
    ...SCENARIO_ORDER.map((id) =>
      h(
        "li",
        null,
        h(
          "button",
          {
            type: "button",
            class: "sc-btn",
            "data-id": id,
            onclick: () => runManual(id),
          },
          SCENARIOS[id].title,
        ),
      ),
    ),
  );
}

function renderScenarioButtons() {
  const disabled = !S.gw.mandate || S.busy;
  for (const b of document.querySelectorAll(".sc-btn")) {
    const id = b.dataset.id;
    b.disabled = disabled;
    b.classList.toggle("is-active", S.activeScenario === id);
    b.classList.toggle("is-done", S.done.has(id));
    const label = SCENARIOS[id].title;
    if (S.done.has(id)) b.replaceChildren(icon("check"), label);
    else b.replaceChildren(label);
  }
  const hint = $("sc-hint");
  if (!S.gw.mandate)
    hint.textContent =
      "Сначала подпишите мандат в панели 1 или запустите весь сценарий.";
  else if (S.running)
    hint.textContent =
      "Идёт сценарий. «Остановить» прервёт его, «Сбросить демо» вернёт всё к началу.";
  else
    hint.textContent =
      "Каждая ситуация - на 10 минут позже предыдущей по часам симуляции.";
}

function renderAgentStatus() {
  const gw = S.gw;
  const pill = $("agent-status");
  let tone = "neutral";
  let text = "Ждёт мандата";
  if (gw.state.kill_switch) {
    tone = "deny";
    text = "Выключен";
  } else if (!gw.mandate) {
    tone = "neutral";
  } else if (gw.state.paused) {
    tone = "ask";
    text = "На паузе";
  } else {
    tone = "ok";
    text = "Работает";
  }
  pill.dataset.tone = tone;
  pill.textContent = text;
  pill.classList.toggle("is-live", tone === "ok");
  const kill = $("btn-kill");
  kill.setAttribute("aria-checked", String(gw.state.kill_switch));
  kill.querySelector(".switch-label").textContent = gw.state.kill_switch
    ? "Агент выключен"
    : "Выключатель агента";
  const resume = $("btn-resume");
  resume.disabled = !gw.state.paused;
  setButton(
    $("btn-run-all"),
    S.running ? "stop" : "play",
    S.running ? "Остановить" : "Пройти весь сценарий",
  );
  renderScenarioButtons();
  for (const card of S.cardsByPending.values()) card.syncActions();
}

function hideFeedEmpty() {
  $("feed-empty").hidden = true;
}

function scrollFeedTop() {
  const sc = $("feed-scroll");
  if (sc.scrollTop > 0) sc.scrollTo({ top: 0, behavior: smooth() });
}

function addGroup(sc, slot) {
  hideFeedEmpty();
  const body = h("div", { class: "group-body" });
  const idx = SCENARIO_ORDER.indexOf(sc.id) + 1;
  const el = h(
    "section",
    { class: "group enter", "aria-label": sc.title },
    h(
      "div",
      { class: "group-head" },
      h(
        "div",
        { class: "group-meta" },
        h("time", null, clock(slot)),
        h("span", null, `Ситуация ${idx} из ${SCENARIO_ORDER.length}`),
      ),
      h("h3", { class: "group-title" }, sc.title),
      h("p", { class: "group-lead" }, sc.lead),
    ),
    body,
  );
  if (sc.news) {
    body.append(
      h(
        "div",
        { class: "news" },
        icon("news"),
        h(
          "div",
          null,
          h("div", { class: "news-src" }, sc.news.source),
          h("p", { class: "news-text" }, `«${sc.news.text}»`),
        ),
      ),
    );
  }
  $("feed").prepend(el);
  scrollFeedTop();
  return { el, body };
}

function addNote(group, ic, text, tone = "") {
  group.body.append(
    h(
      "div",
      { class: "feed-note enter", "data-tone": tone || null },
      icon(ic),
      h("span", null, text),
    ),
  );
}

function addSys(text, tone, ic) {
  hideFeedEmpty();
  $("feed").prepend(
    h(
      "div",
      { class: "sys enter", "data-tone": tone },
      icon(ic),
      h("time", null, clock(S.gw.now)),
      h("span", null, text),
    ),
  );
  scrollFeedTop();
}

function compactOlder(current) {
  for (const card of document.querySelectorAll("#feed .attempt")) {
    if (card !== current && card.dataset.compactable === "1")
      card.classList.add("is-compact");
  }
  for (const t of document.querySelectorAll(
    "#feed .attempt.is-compact .checks-toggle",
  ))
    t.textContent = "показать все";
}

function failRow(c, prefix = "") {
  const soft = c.severity === "soft";
  return h(
    "li",
    { class: `fail is-${c.severity}` },
    icon(soft ? "ask" : "x"),
    h(
      "div",
      { class: "fail-title" },
      h(
        "span",
        null,
        (prefix ? prefix + " · " : "") + (CHECK_LABELS[c.code] || c.code),
      ),
      h("code", null, c.code),
      h(
        "span",
        {
          class: "fail-tag",
          title: soft
            ? "Мягкое правило: шлюз спрашивает вас"
            : "Жёсткое правило: шлюз запрещает",
        },
        soft ? "спросить вас" : "запрет",
      ),
    ),
    h("p", { class: "fail-detail" }, c.detail),
  );
}

function buildAttemptCard(r, { who, whoIcon }) {
  const seq = r.entry.seq;
  const what = describeAttempt(r);
  const total = r.checks.length;
  const failed = failing(r.checks);
  const el = h("article", {
    class: "attempt enter",
    "data-state": "pending",
    "data-compactable": "1",
  });
  let badgeEl = h("span", { class: "badge badge-wait" }, "Проверка");
  el.append(
    h(
      "div",
      { class: "attempt-head" },
      h("time", null, clock(r.entry.ts)),
      h("span", { class: "attempt-who" }, icon(whoIcon), who),
      badgeEl,
    ),
  );
  el.append(
    h(
      "p",
      { class: "attempt-what" },
      h("span", null, what.title),
      what.value ? h("span", { class: "attempt-value" }, what.value) : null,
    ),
  );
  if (what.sub) el.append(h("p", { class: "attempt-sub" }, what.sub));
  if (what.reason)
    el.append(h("p", { class: "attempt-reason" }, `Агент: «${what.reason}»`));
  const count = h("span", null, `Проверки шлюза: ${total}`);
  const toggle = h(
    "button",
    { type: "button", class: "link-btn checks-toggle", hidden: true },
    "свернуть",
  );
  el.append(h("div", { class: "checks-head" }, count, toggle));
  const pills = r.checks.map((c) =>
    h(
      "li",
      { class: "pill is-wait", title: `${c.code}: ${c.detail}` },
      icon("dot"),
      CHECK_LABELS[c.code] || c.code,
    ),
  );
  el.append(
    h("ul", { class: "checks", "aria-label": "Проверки шлюза" }, pills),
  );
  const fails = h(
    "ul",
    { class: "fails", hidden: true },
    failed.map((c) => failRow(c)),
  );
  const expl = h("p", { class: "expl", hidden: true }, r.explanation);
  const actions = h("div", { class: "card-actions", hidden: true });
  const foot = h(
    "div",
    { class: "card-foot", hidden: true },
    h(
      "button",
      { type: "button", class: "link-btn", onclick: () => focusJournal(seq) },
      `Запись #${seq} в журнале`,
    ),
    r.order && r.order.order_id
      ? h("span", null, `заявка ${r.order.order_id}`)
      : null,
    r.broker ? h("span", null, `брокер ${r.broker.order_id}`) : null,
  );
  el.append(fails, expl, actions, foot);

  toggle.addEventListener("click", () => {
    el.classList.toggle("is-compact");
    toggle.textContent = el.classList.contains("is-compact")
      ? "показать все"
      : "свернуть";
  });

  const setBadge = (decision, text) => {
    const next = badge(decision, text);
    next.classList.add("pop-in");
    badgeEl.replaceWith(next);
    badgeEl = next;
  };

  let confirmBtn = null;
  let rejectBtn = null;
  let actionHint = null;
  const api = {
    el,
    get confirmBtn() {
      return confirmBtn;
    },
    syncActions() {
      if (!confirmBtn || !S.gw.pending.has(r.pendingId)) return;
      const st = S.gw.state;
      confirmBtn.disabled = st.kill_switch || st.paused;
      actionHint.textContent = st.kill_switch
        ? "Агент выключен: подтвердить можно, когда вы его включите."
        : st.paused
          ? "Агент на паузе: подтвердить можно после снятия паузы."
          : "Шлюз перепроверит жёсткие правила на свежих данных и только потом отправит заявку.";
    },
    lock() {
      if (confirmBtn) confirmBtn.disabled = true;
      if (rejectBtn) rejectBtn.disabled = true;
    },
    applyConfirm(res) {
      const ok = res.decision === "ALLOW";
      actions.hidden = true;
      el.dataset.state = ok ? "ALLOW" : "DENY";
      setBadge(ok ? "ALLOW" : "DENY", ok ? "Исполнено" : "Отклонено");
      const hard = res.checks.length;
      const note = ok
        ? `Вы подтвердили в ${clock(res.entry.ts)}. Шлюз перепроверил ${hard} ${plural(hard, "жёсткое правило", "жёстких правила", "жёстких правил")} на свежих данных: всё в порядке, заявка исполнена (брокер ${res.broker.order_id}).`
        : `Вы подтвердили, но ${res.explanation.charAt(0).toLowerCase()}${res.explanation.slice(1)}`;
      foot.before(h("p", { class: "card-note pop-in" }, note));
      foot.append(
        h(
          "button",
          {
            type: "button",
            class: "link-btn",
            onclick: () => focusJournal(res.entry.seq),
          },
          `Подтверждение #${res.entry.seq}`,
        ),
      );
      S.cardsByPending.delete(r.pendingId);
    },
    applyReject(res) {
      actions.hidden = true;
      el.dataset.state = "REJECTED";
      setBadge("REJECTED");
      foot.before(
        h(
          "p",
          { class: "card-note pop-in" },
          `Вы отклонили заявку в ${clock(res.entry.ts)}: она не ушла брокеру.`,
        ),
      );
      foot.append(
        h(
          "button",
          {
            type: "button",
            class: "link-btn",
            onclick: () => focusJournal(res.entry.seq),
          },
          `Отказ #${res.entry.seq}`,
        ),
      );
      S.cardsByPending.delete(r.pendingId);
    },
    async play(token) {
      const step = reduceMotion.matches
        ? 0
        : Math.max(26, Math.min(52, 880 / Math.max(total, 1)));
      for (let i = 0; i < pills.length; i++) {
        const c = r.checks[i];
        pills[i].className =
          "pill is-hit " + (c.ok ? "is-ok" : `is-fail is-${c.severity}`);
        pills[i].firstChild.replaceWith(
          icon(c.ok ? "check" : c.severity === "soft" ? "ask" : "x"),
        );
        if (step) await pace(step, token);
      }
      if (step) await pace(180, token);
      el.dataset.state = r.decision;
      setBadge(r.decision);
      count.textContent = failed.length
        ? `Проверки шлюза: ${total}, не прошли: ${failed.length}`
        : `Проверки шлюза: ${total}, все пройдены`;
      toggle.hidden = false;
      fails.hidden = !failed.length;
      expl.hidden = false;
      foot.hidden = false;
      if (r.decision === "ASK" && S.gw.pending.has(r.pendingId)) {
        actions.hidden = false;
        api.syncActions();
      }
    },
  };

  if (r.decision === "ASK" && r.pendingId) {
    confirmBtn = h(
      "button",
      {
        type: "button",
        class: "btn btn-primary btn-sm",
        onclick: () => onConfirm(r.pendingId),
      },
      icon("check"),
      "Подтвердить",
    );
    rejectBtn = h(
      "button",
      {
        type: "button",
        class: "btn btn-secondary btn-sm",
        onclick: () => onReject(r.pendingId),
      },
      "Отклонить",
    );
    actionHint = h("span", { class: "hint" });
    actions.append(confirmBtn, rejectBtn, actionHint);
    S.cardsByPending.set(r.pendingId, api);
  }
  return api;
}

async function showAttempt(group, r, { who, whoIcon, token }) {
  const card = buildAttemptCard(r, { who, whoIcon });
  group.body.append(card.el);
  compactOlder(card.el);
  scrollFeedTop();
  await card.play(token);
  renderAll();
  notify(r);
  return card;
}

function addRunawayCard(group, ev) {
  const o = ev.step.order;
  const inst = MARKET.instruments[o.ticker];
  const n = ev.count;
  const perMin = S.gw.mandate.limits.max_orders_per_minute;
  const el = h("article", { class: "attempt enter", "data-state": "pending" });
  let badgeEl = h("span", { class: "badge badge-wait" }, "Поток заявок");
  el.append(
    h(
      "div",
      { class: "attempt-head" },
      h("time", null, clock(ev.result.entry.ts)),
      h("span", { class: "attempt-who" }, icon("loop"), "Агент в цикле"),
      badgeEl,
    ),
  );
  el.append(
    h(
      "p",
      { class: "attempt-what" },
      h(
        "span",
        null,
        `${n} раз «Купить ${displayName(o.ticker, inst)} ×\u00a0${o.quantity} по ${fmtNumber(o.price, 4)}\u00a0₽»`,
      ),
    ),
  );
  el.append(
    h(
      "p",
      { class: "attempt-sub" },
      `Одна и та же заявка каждые ${fmtNumber(ev.step.every, 2)} с. ${perMin ? `Лимит мандата: ${perMin} в минуту.` : "Лимита частоты в мандате нет."}`,
    ),
  );
  const vSent = h("span", { class: "rw-value" }, "0");
  const vReached = h("span", { class: "rw-value" }, "0");
  const vBlocked = h("span", { class: "rw-value" }, "0");
  el.append(
    h(
      "div",
      { class: "rw-stats" },
      h(
        "div",
        { class: "rw-stat" },
        vSent,
        h("span", { class: "rw-label" }, "отправлено агентом"),
      ),
      h(
        "div",
        { class: "rw-stat rw-reached" },
        vReached,
        h("span", { class: "rw-label" }, "дошло до брокера"),
      ),
      h(
        "div",
        { class: "rw-stat rw-blocked" },
        vBlocked,
        h("span", { class: "rw-label" }, "остановлено шлюзом"),
      ),
    ),
  );
  const cells = Array.from({ length: n }, (_, i) =>
    h("button", {
      type: "button",
      class: "cell",
      tabindex: "-1",
      "aria-label": `Заявка ${i + 1}`,
    }),
  );
  el.append(
    h(
      "div",
      { class: "cells", "aria-label": "Заявки робота по порядку" },
      cells,
    ),
  );
  el.append(
    h(
      "div",
      { class: "cells-legend" },
      h("span", null, h("i", { style: "background: var(--ok)" }), "исполнена"),
      h(
        "span",
        null,
        h("i", { style: "background: var(--deny)" }),
        "поймано зацикливание",
      ),
      h(
        "span",
        null,
        h("i", { style: "background: var(--deny-border)" }),
        "отклонена: пауза",
      ),
    ),
  );
  const fails = h("ul", { class: "fails", hidden: true });
  const expl = h("p", { class: "expl", hidden: true });
  const foot = h("div", { class: "card-foot", hidden: true });
  el.append(fails, expl, foot);
  group.body.append(el);
  compactOlder(el);
  scrollFeedTop();

  let sent = 0;
  let reached = 0;
  let blocked = 0;
  let runawayAt = null;
  let pausedShown = false;
  let firstSeq = null;
  let lastSeq = null;
  return {
    push(r) {
      const i = sent;
      sent += 1;
      firstSeq = firstSeq ?? r.entry.seq;
      lastSeq = r.entry.seq;
      const cell = cells[i];
      const f = r.checks.find((c) => !c.ok && c.severity === "hard");
      if (r.decision === "ALLOW") {
        reached += 1;
        cell.classList.add("is-allow");
      } else {
        blocked += 1;
        cell.classList.add(
          f && f.code === "RUNAWAY" ? "is-runaway" : "is-paused",
        );
      }
      if (f && f.code === "RUNAWAY") {
        runawayAt = i + 1;
        fails.hidden = false;
        fails.append(failRow(f, `${i + 1}-я заявка`));
      } else if (f && !pausedShown) {
        pausedShown = true;
        fails.hidden = false;
        fails.append(failRow(f, `С ${i + 1}-й заявки`));
      }
      cell.title = `#${i + 1} · ${clock(r.entry.ts, true)} · ${r.decision}${f ? " · " + f.code : ""}`;
      cell.onclick = () => focusJournal(r.entry.seq);
      vSent.textContent = String(sent);
      vReached.textContent = String(reached);
      vBlocked.textContent = String(blocked);
    },
    finish() {
      el.dataset.state = blocked ? "DENY" : "ALLOW";
      const next = runawayAt
        ? badge("DENY", "Цикл остановлен")
        : blocked
          ? badge("DENY", "Отклонено")
          : badge("ALLOW", "Исполнено");
      next.classList.add("pop-in");
      badgeEl.replaceWith(next);
      badgeEl = next;
      expl.hidden = false;
      if (runawayAt) {
        expl.textContent = `Шлюз остановил цикл на ${runawayAt}-й заявке и поставил агента на паузу: до брокера дошло ${reached} из ${sent}.`;
      } else if (!reached) {
        expl.textContent = `До брокера не дошла ни одна из ${sent} заявок: агент уже стоит на паузе или выключен.`;
      } else {
        expl.textContent = `До брокера дошло ${reached} из ${sent}.`;
      }
      foot.hidden = false;
      foot.append(
        h("span", null, `Записи #${firstSeq}-#${lastSeq} в журнале`),
        h(
          "button",
          {
            type: "button",
            class: "link-btn",
            onclick: () =>
              focusJournal(firstSeq + (runawayAt ? runawayAt - 1 : 0)),
          },
          runawayAt ? `открыть #${firstSeq + runawayAt - 1}` : "открыть",
        ),
      );
    },
  };
}

async function onConfirm(pendingId) {
  if (!S.gw.pending.has(pendingId)) return;
  const card = S.cardsByPending.get(pendingId);
  if (card) card.lock();
  const res = await S.gw.confirm(pendingId);
  if (card) card.applyConfirm(res);
  if (res.decision === "DENY")
    toast({
      tone: "deny",
      ic: "ban",
      title: "Заявка не исполнена",
      text: res.explanation,
    });
  renderAll();
}

async function onReject(pendingId) {
  if (!S.gw.pending.has(pendingId)) return;
  const card = S.cardsByPending.get(pendingId);
  if (card) card.lock();
  const res = await S.gw.reject(pendingId);
  if (card) card.applyReject(res);
  renderAll();
}

async function onKill() {
  const on = !S.gw.state.kill_switch;
  await S.gw.killSwitch(on);
  addSys(
    on
      ? "Вы включили выключатель: агент выключен, ни одна его заявка не дойдёт до брокера."
      : "Вы выключили выключатель: агент снова работает в рамках мандата.",
    on ? "deny" : "ok",
    "power",
  );
  toast(
    on
      ? {
          tone: "deny",
          ic: "power",
          title: "Агент выключен",
          text: "Пока выключатель включён, шлюз отклоняет любую заявку агента первой же проверкой.",
        }
      : {
          tone: "ok",
          ic: "power",
          title: "Агент снова включён",
          text: "Шлюз пропускает заявки в рамках мандата.",
        },
  );
  renderAll();
}

async function onResume() {
  if (!S.gw.state.paused) return;
  await S.gw.resume();
  addSys(
    "Вы сняли паузу: счётчик нарушений обнулён, агент снова в работе.",
    "ok",
    "play",
  );
  renderAll();
}

function nextStep(sc, step) {
  const i = sc.steps.indexOf(step);
  return i >= 0 ? sc.steps[i + 1] : null;
}

async function runScenarioUI(sc, { auto, token }) {
  let group = null;
  let rw = null;
  for await (const ev of runScenario(S.gw, sc, { client: auto })) {
    if (token !== runToken) throw ABORT;
    switch (ev.type) {
      case "scene":
        group = addGroup(sc, ev.slot);
        renderClock();
        if (sc.id === "S5" && !auto && S.gw.state.paused) {
          addNote(
            group,
            "info",
            "Агент сейчас на паузе: все заявки робота отклонятся ещё до проверки частоты. Снимите паузу, чтобы увидеть, как шлюз ловит зацикливание.",
          );
        }
        await pace(auto ? 650 : 220, token);
        break;
      case "resume":
        addNote(
          group,
          "play",
          "Клиент снял паузу: счётчик нарушений обнулён, агент снова в работе.",
          "ok",
        );
        renderAll();
        await pace(auto ? 900 : 300, token);
        break;
      case "coupon": {
        addNote(
          group,
          "coin",
          `Пришёл купон ${fmtMoney(ev.result.amount)}: деньги уже на счёте.`,
          "accent",
        );
        renderPortfolio();
        renderClock();
        await pace(auto ? 750 : 350, token);
        if (ev.result.outcome) {
          await showAttempt(group, ev.result.outcome, {
            who: `Поручение «${ev.result.standing.id}»`,
            whoIcon: "calendar",
            token,
          });
        } else {
          addNote(
            group,
            "info",
            ev.result.standing
              ? "Купона не хватает даже на одну бумагу: агент ничего не покупает."
              : "В мандате нет поручения на реинвест: агент ничего не покупает.",
          );
        }
        break;
      }
      case "monthly":
        if (!ev.result.items.length)
          addNote(
            group,
            "info",
            "В мандате нет поручения на это число: агент ничего не покупает.",
          );
        for (const item of ev.result.items) {
          await showAttempt(group, item.outcome, {
            who: `Поручение «${item.standing.id}»`,
            whoIcon: "calendar",
            token,
          });
        }
        break;
      case "decision":
        await showAttempt(group, ev.result, {
          who: "Агент",
          whoIcon: "bot",
          token,
        });
        if (auto && ev.result.decision === "ASK") {
          const nx = nextStep(sc, ev.step);
          if (nx && nx.kind === "confirm") {
            narrateText(
              "Клиенту пришло уведомление. Он смотрит на заявку и подтверждает её одной кнопкой.",
            );
            await pace(1700, token);
          }
        }
        break;
      case "confirm": {
        const card = S.cardsByPending.get(ev.result.pendingId);
        if (card) {
          autopress(card.confirmBtn);
          await pace(500, token);
          card.applyConfirm(ev.result);
        }
        renderAll();
        await pace(auto ? 700 : 0, token);
        break;
      }
      case "burst":
        if (!rw) rw = addRunawayCard(group, ev);
        rw.push(ev.result);
        renderClock();
        renderMetrics();
        renderJournal();
        renderAgentStatus();
        if (ev.result.decision === "ALLOW") renderPortfolio();
        if (ev.result.pausedNow) notify(ev.result);
        await pace(reduceMotion.matches ? 20 : 90, token);
        if (ev.i === ev.count - 1) {
          rw.finish();
          renderAll();
        }
        break;
      default:
        break;
    }
  }
}

async function runManual(id) {
  if (S.busy || !S.gw.mandate) return;
  const token = runToken;
  S.busy = true;
  S.activeScenario = id;
  renderAll();
  try {
    await runScenarioUI(SCENARIOS[id], { auto: false, token });
    S.done.add(id);
  } catch (e) {
    if (e !== ABORT) {
      console.error(e);
      toast({
        tone: "deny",
        ic: "alert",
        title: "Сценарий прервался",
        text: String(e.message || e),
      });
    }
  } finally {
    if (token === runToken) {
      S.busy = false;
      S.activeScenario = null;
      renderAll();
    }
  }
}

// ------------------------------------------------------------ полный прогон

function narrate(step, total, text) {
  $("narrator").hidden = false;
  $("narrator-step").textContent =
    step === 0
      ? "Подготовка: мандат"
      : step > total
        ? "Готово"
        : `Ситуация ${step} из ${total}`;
  $("narrator-bar").style.width =
    `${Math.round(((Math.min(step, total) + 1) / (total + 1)) * 100)}%`;
  narrateText(text);
}

function narrateText(text) {
  $("narrator-text").textContent = text;
}

function revealPanel(id) {
  if (narrow.matches)
    $(id).scrollIntoView({ block: "start", behavior: smooth() });
}

function finalLine() {
  const st = S.gw.stats;
  const n = st.wouldViolate;
  return `Без Мандата до брокера дошло бы ${n} ${plural(n, "нарушение", "нарушения", "нарушений")}, с Мандатом - ни одного без вашего подтверждения. Дальше журнал: проверьте цепочку, подделайте запись, сделайте Replay.`;
}

function stopRun() {
  runToken += 1;
  $("narrator").classList.remove("is-floating");
  S.running = false;
  S.busy = false;
  S.activeScenario = null;
  narrateText(
    "Сценарий остановлен. Можно продолжить по одной ситуации или сбросить демо.",
  );
  renderAll();
}

async function runAll() {
  if (S.running) {
    stopRun();
    return;
  }
  const token = resetDemo();
  S.running = true;
  S.busy = true;
  $("sc-details").open = false; // во время прогона ленте нужно место
  $("narrator").classList.add("is-floating");
  renderAll();
  const total = SCENARIO_ORDER.length;
  try {
    narrate(
      0,
      total,
      "Клиент описал границы своими словами. Собираем из них правила.",
    );
    revealPanel("p-mandate");
    await pace(1300, token);
    autopress($("btn-compile"));
    compile();
    await pace(500, token);
    narrateText(
      "Слова стали правилами: что агент сможет, чего не сможет и о чём спросит вас.",
    );
    $("rules").scrollIntoView({ block: "start", behavior: smooth() });
    await pace(2400, token);
    const fixBtn = document.querySelector(`[data-fix="${DEMO_FIX_CODE}"]`);
    narrate(
      0,
      total,
      "Стресс-тест нашёл дыру: нет дневного лимита. Клиент закрывает её одной кнопкой: 100 000 ₽ в день.",
    );
    if (fixBtn) fixBtn.scrollIntoView({ block: "nearest", behavior: smooth() });
    await pace(1800, token);
    autopress(fixBtn);
    fixFinding(DEMO_FIX_CODE);
    await pace(1400, token);
    narrate(
      0,
      total,
      "Критичных дыр нет. Клиент подписывает мандат: хеш правил становится первой записью журнала.",
    );
    $("btn-sign").scrollIntoView({ block: "nearest", behavior: smooth() });
    await pace(1000, token);
    autopress($("btn-sign"));
    S.busy = false;
    await sign();
    S.busy = true;
    await pace(1600, token);
    revealPanel("p-agent");
    for (let i = 0; i < SCENARIO_ORDER.length; i++) {
      const id = SCENARIO_ORDER[i];
      const sc = SCENARIOS[id];
      if (id === "S5" && S.gw.state.paused) {
        narrate(
          i + 1,
          total,
          "Клиент снимает паузу, агент снова в работе. И тут у него ломается цикл.",
        );
        await pace(1600, token);
      }
      narrate(i + 1, total, `${sc.title}. ${sc.lead}`);
      S.activeScenario = id;
      renderScenarioButtons();
      await runScenarioUI(sc, { auto: true, token });
      S.done.add(id);
      S.activeScenario = null;
      renderScenarioButtons();
      if (id === "S4")
        narrateText(
          "Три отказа за 10 минут: шлюз сам ставит агента на паузу и шлёт вам уведомление.",
        );
      await pace(id === "S4" ? 2200 : 1100, token);
    }
    narrate(total + 1, total, finalLine());
  } catch (e) {
    if (e !== ABORT) {
      console.error(e);
      toast({
        tone: "deny",
        ic: "alert",
        title: "Сценарий прервался",
        text: String(e.message || e),
      });
    }
  } finally {
    if (token === runToken) {
      S.running = false;
      S.busy = false;
      S.activeScenario = null;
      $("narrator").classList.remove("is-floating");
      renderAll();
    }
  }
}

// ------------------------------------------------------------ 3. журнал

function renderMetrics() {
  const st = S.gw.stats;
  const pend = S.gw.pending.size;
  const denied = st.denied + st.rejected;
  const metric = (value, label, tone) =>
    h(
      "div",
      { class: "metric", "data-tone": tone || null },
      h("span", { class: "metric-value" }, String(value)),
      h("span", { class: "metric-label" }, label),
    );
  $("metrics").replaceChildren(
    metric(
      st.attempts,
      plural(st.attempts, "попытка агента", "попытки агента", "попыток агента"),
    ),
    metric(st.executed, "исполнено", "ok"),
    metric(pend, pend === 1 ? "ждёт вас" : "ждут вас", pend ? "ask" : null),
    metric(denied, "отклонено", denied ? "deny" : null),
  );
  const n = st.wouldViolate;
  $("compare").replaceChildren(
    h(
      "div",
      {
        class: "cmp cmp-without",
        "aria-label": `Без Мандата до брокера дошло бы нарушений: ${n}`,
      },
      h("span", { class: "cmp-value" }, String(n)),
      h(
        "span",
        { class: "cmp-label" },
        h("b", null, "Без Мандата"),
        "до брокера дошло бы нарушений",
      ),
    ),
    h(
      "div",
      {
        class: "cmp cmp-with",
        "aria-label": `С Мандатом: ${st.slipped} без вашего подтверждения`,
      },
      h("span", { class: "cmp-value" }, String(st.slipped)),
      h(
        "span",
        { class: "cmp-label" },
        h("b", null, "С Мандатом"),
        "без вашего подтверждения",
      ),
    ),
  );
}

function orderShort(o) {
  return `${o.side === "sell" ? "Продать" : "Купить"} ${o.ticker} × ${fmtNumber(o.quantity, 0)}`;
}

function journalSummary(e) {
  const p = e.payload;
  const fails = (checks) => {
    const f = failing(checks).map((c) => c.code);
    return f.length ? ` · ${f.join(", ")}` : "";
  };
  switch (e.type) {
    case "mandate_signed":
      return `Мандат «${p.title}» подписан клиентом`;
    case "decision":
      return p.order
        ? orderShort(p.order) + fails(p.checks)
        : "Агент: update_mandate · MANDATE_CHANGE";
    case "confirm":
      return `Подтверждена #${p.ref_seq}: ${orderShort(p.order)}${p.decision === "DENY" ? fails(p.checks) : ""}`;
    case "reject":
      return `Отклонена #${p.ref_seq}: ${orderShort(p.order)}`;
    case "kill":
      return p.on ? "Клиент выключил агента" : "Клиент снова включил агента";
    case "resume":
      return "Клиент снял паузу";
    default:
      return e.type;
  }
}

function journalBadge(e) {
  const p = e.payload;
  if (e.type === "decision" || e.type === "confirm") return badge(p.decision);
  if (e.type === "reject") return badge("REJECTED");
  if (e.type === "mandate_signed")
    return h("span", { class: "badge badge-accent" }, "Подпись");
  if (e.type === "kill")
    return h(
      "span",
      { class: `badge ${p.on ? "badge-deny" : "badge-neutral"}` },
      p.on ? "Выключен" : "Включён",
    );
  return h("span", { class: "badge badge-neutral" }, "Пауза снята");
}

function fillJournalRow(row, e) {
  row.replaceChildren(
    h("span", { class: "j-seq" }, `#${e.seq}`),
    h(
      "span",
      { class: "j-main" },
      h(
        "span",
        { class: "j-line1" },
        h("time", null, clock(e.ts, true)),
        h("span", { class: "j-type" }, TYPE_RU[e.type] || e.type),
        journalBadge(e),
      ),
      h("span", { class: "j-sum" }, journalSummary(e)),
      h(
        "span",
        { class: "j-hashes", title: "хеш записи ← хеш предыдущей записи" },
        `${e.hash.slice(0, 10)} ← ${e.prev_hash.slice(0, 10)}`,
      ),
    ),
  );
}

function buildJournalItem(e) {
  const row = h("button", {
    type: "button",
    class: "j-row",
    "aria-expanded": "false",
    "aria-controls": `jd-${e.seq}`,
  });
  fillJournalRow(row, e);
  const details = h("div", {
    class: "j-details",
    id: `jd-${e.seq}`,
    hidden: true,
  });
  row.addEventListener("click", () => toggleJournal(e.seq));
  const li = h("li", { class: "j-item" }, row, details);
  return { li, row, details };
}

function renderJournal() {
  const entries = S.gw.journal.entries;
  const list = $("j-list");
  const firstPaint = S.journalRendered === 0;
  for (let i = S.journalRendered; i < entries.length; i++) {
    const item = buildJournalItem(entries[i]);
    if (!firstPaint || i > 0) item.li.classList.add("is-new");
    list.prepend(item.li);
    S.journalItems.set(entries[i].seq, item);
  }
  S.journalRendered = entries.length;
  const n = entries.length;
  $("journal-empty").hidden = n > 0;
  const count = $("journal-count");
  count.textContent = `${n} ${plural(n, "запись", "записи", "записей")}`;
  count.dataset.tone = n ? "accent" : "neutral";
  $("btn-verify").disabled = n === 0;
  $("btn-tamper").disabled =
    !entries.some((x) => x.type === "decision") || S.tamperedSeq !== null;
}

function kv(rows) {
  return h(
    "dl",
    { class: "jd-kv" },
    rows.filter(Boolean).map(([k, v]) => [h("dt", null, k), h("dd", null, v)]),
  );
}

function section(title, body) {
  return h("div", { class: "jd-section" }, h("h4", null, title), body);
}

function mono(text) {
  return h("span", { class: "mono" }, text);
}

function checksList(checks) {
  return h(
    "ul",
    { class: "jd-checks" },
    checks.map((c) =>
      h(
        "li",
        { class: `jd-check${c.ok ? "" : " is-fail"} is-${c.severity}` },
        icon(c.ok ? "check" : c.severity === "soft" ? "ask" : "x"),
        h(
          "div",
          null,
          h(
            "div",
            { class: "jd-check-top" },
            h("b", null, CHECK_LABELS[c.code] || c.code),
            h(
              "code",
              null,
              `${c.code} · ${c.severity === "soft" ? "мягкое" : "жёсткое"}`,
            ),
          ),
          h("div", { class: "jd-check-detail" }, c.detail),
        ),
      ),
    ),
  );
}

function snapshotRows(s) {
  const inst = s.instrument;
  const book = s.book;
  const lvl = (side) =>
    book && book[side] && book[side].length
      ? `${fmtNumber(book[side][0][0], 4)} (${fmtNumber(book[side][0][1], 0)} лотов)`
      : "-";
  return [
    ["Свободные деньги", fmtMoney(s.cash_rub)],
    ["Портфель", fmtMoney(s.total_value)],
    inst
      ? [
          "Бумага",
          `${inst.name} · ${inst.ticker} · ${CLASS_ONE[inst.class] || inst.class} · лот ${inst.lot} · ${String(inst.currency).toUpperCase()}`,
        ]
      : ["Бумага", `${s.ticker}: нет у брокера`],
    inst
      ? [
          "Последняя цена",
          `${fmtNumber(inst.last, 4)}${inst.currency !== "rub" ? ` ${inst.currency.toUpperCase()} · курс ${fmtNumber(s.fx, 2)} ₽` : " ₽"}`,
        ]
      : null,
    inst
      ? [
          "Позиция",
          `${fmtNumber(s.position_qty, 0)} шт. · ${fmtMoney(s.position_value)}`,
        ]
      : null,
    inst
      ? [
          "Стакан",
          book ? `продажа ${lvl("asks")} · покупка ${lvl("bids")}` : "нет",
        ]
      : null,
  ];
}

function stateRows(st) {
  return [
    ["Оборот за день", fmtMoney(st.turnover_today)],
    ["Заявок за минуту", String(st.orders_last_minute)],
    ["Пауза", st.paused ? "да" : "нет"],
    ["Выключатель", st.kill_switch ? "включён" : "нет"],
    st.order_id_seen !== undefined
      ? ["Повтор номера", st.order_id_seen ? "да" : "нет"]
      : null,
  ];
}

function buildDetails(e) {
  const p = e.payload;
  const out = [];
  if (p.explanation) out.push(h("p", { class: "jd-expl" }, p.explanation));
  if (e.type === "decision" || e.type === "confirm") {
    out.push(
      section(
        `Проверки (${p.checks.length})${e.type === "confirm" ? ": только жёсткие, на свежем снимке" : ""}`,
        checksList(p.checks),
      ),
    );
    if (p.request)
      out.push(
        section(
          "Запрос агента",
          kv([
            ["Инструмент", mono(p.request.tool)],
            ["Изменения", mono(JSON.stringify(p.request.changes))],
          ]),
        ),
      );
    if (p.snapshot)
      out.push(section("Снимок рынка", kv(snapshotRows(p.snapshot))));
    out.push(
      section("Состояние шлюза до проверки", kv(stateRows(p.state_before))),
    );
    out.push(
      section(
        "Брокер",
        kv([
          [
            "Заявка",
            p.broker ? `${p.broker.order_id} · исполнена` : "не отправлялась",
          ],
        ]),
      ),
    );
  } else if (e.type === "mandate_signed") {
    out.push(
      section(
        "Мандат",
        kv([
          ["Название", p.title],
          ["Хеш правил", mono(p.mandate_hash)],
          [
            "Стресс-тест",
            p.findings.length ? p.findings.join(", ") : "без замечаний",
          ],
        ]),
      ),
    );
  } else if (e.type === "reject" || e.type === "kill") {
    if (p.order)
      out.push(
        section(
          "Заявка",
          kv([
            ["Что", orderShort(p.order)],
            ["Номер", p.order.order_id || "-"],
          ]),
        ),
      );
  }
  const prev =
    e.seq > 1
      ? h(
          "span",
          null,
          h(
            "button",
            {
              type: "button",
              class: "link-btn",
              onclick: () => focusJournal(e.seq - 1),
            },
            `запись #${e.seq - 1}`,
          ),
          h("br"),
          mono(e.prev_hash),
        )
      : mono(`${e.prev_hash.slice(0, 16)}… (начало цепочки)`);
  out.push(
    section(
      "Цепочка",
      kv([
        ["Хеш записи", mono(e.hash)],
        ["Предыдущая", prev],
      ]),
    ),
  );
  if (e.type === "decision" || e.type === "confirm") {
    const result = h("div");
    const btn = h(
      "button",
      {
        type: "button",
        class: "btn btn-secondary btn-sm",
        onclick: () => doReplay(e.seq, result, btn),
      },
      icon("replay"),
      "Replay",
    );
    out.push(
      h(
        "div",
        { class: "jd-actions" },
        btn,
        h(
          "span",
          { class: "hint" },
          "заново прогнать движок на сохранённых данных",
        ),
      ),
      result,
    );
  }
  out.push(
    h(
      "details",
      { class: "jd-section jd-raw" },
      h("summary", null, "Запись целиком (JSON)"),
      h("pre", null, JSON.stringify(e, null, 2)),
    ),
  );
  return out;
}

function closeJournal(seq) {
  const item = S.journalItems.get(seq);
  if (item) {
    item.row.setAttribute("aria-expanded", "false");
    item.details.hidden = true;
    item.details.replaceChildren();
  }
  if (S.openSeq === seq) S.openSeq = null;
}

function toggleJournal(seq, { forceOpen = false } = {}) {
  const item = S.journalItems.get(seq);
  if (!item) return;
  if (S.openSeq === seq && !forceOpen) {
    closeJournal(seq);
    return;
  }
  if (S.openSeq !== null && S.openSeq !== seq) closeJournal(S.openSeq);
  S.openSeq = seq;
  item.row.setAttribute("aria-expanded", "true");
  item.details.replaceChildren(...buildDetails(S.gw.journal.get(seq)));
  item.details.hidden = false;
}

function focusJournal(seq) {
  renderJournal();
  const item = S.journalItems.get(seq);
  if (!item) return;
  toggleJournal(seq, { forceOpen: true });
  item.li.scrollIntoView({ block: "start", behavior: smooth() });
  item.row.focus({ preventScroll: true });
}

async function doReplay(seq, box, btn) {
  btn.disabled = true;
  const r = await S.gw.journal.replay(seq);
  btn.disabled = false;
  let text;
  if (r.match) {
    const n = r.replayed.checks.length;
    text = `Решение совпало: движок заново прогнал ${n} ${plural(n, "проверку", "проверки", "проверок")} на сохранённых данных и получил тот же ${r.replayed.decision}. Мандат сверен по хешу.`;
  } else {
    const parts = [];
    if (r.stored && r.replayed && !r.sameDecision)
      parts.push(
        `в журнале ${r.stored.decision}, а движок на тех же данных даёт ${r.replayed.decision}`,
      );
    if (r.sameChecks === false) parts.push("коды проверок отличаются");
    if (r.mandateOk === false) parts.push("мандат не сходится с хешем");
    if (r.reason) parts.push(r.reason);
    text = `Не совпало: ${parts.join("; ")}. Запись изменили после решения.`;
  }
  box.replaceChildren(
    h(
      "div",
      { class: `replay-result pop-in${r.match ? "" : " is-bad"}` },
      icon(r.match ? "check" : "alert"),
      h("p", null, text),
    ),
  );
}

function setVerify(tone, ic, ...content) {
  const box = $("verify-status");
  box.hidden = false;
  box.dataset.tone = tone;
  box.replaceChildren(icon(ic), h("p", null, ...content));
}

async function doVerify() {
  const btn = $("btn-verify");
  btn.disabled = true;
  const broken = await S.gw.journal.verify();
  btn.disabled = false;
  if (S.brokenSeq !== null) {
    const old = S.journalItems.get(S.brokenSeq);
    if (old) old.li.classList.remove("is-broken");
    S.brokenSeq = null;
  }
  const n = S.gw.journal.length;
  if (broken === null) {
    setVerify(
      "ok",
      "check",
      h("b", null, "Цепочка цела. "),
      `${n} ${plural(n, "запись", "записи", "записей")}: каждый хеш пересчитан и совпал, ссылки на предыдущие записи на месте.`,
    );
    return;
  }
  S.brokenSeq = broken;
  const item = S.journalItems.get(broken);
  if (item) item.li.classList.add("is-broken");
  setVerify(
    "deny",
    "alert",
    h("b", null, `Цепочка нарушена на записи #${broken}. `),
    "Её содержимое больше не совпадает с хешем: запись изменили после того, как она попала в журнал.",
  );
  focusJournal(broken);
}

function doTamper() {
  const entries = S.gw.journal.entries;
  const e = [...entries].reverse().find((x) => x.type === "decision");
  if (!e || S.tamperedSeq !== null) return;
  const before = e.payload.decision;
  const after = before === "ALLOW" ? "DENY" : "ALLOW";
  e.payload.decision = after;
  e.payload.explanation =
    after === "ALLOW"
      ? "В рамках мандата: заявка отправлена брокеру."
      : "Отклонено.";
  S.tamperedSeq = e.seq;
  const item = S.journalItems.get(e.seq);
  if (item) {
    fillJournalRow(item.row, e);
    if (S.openSeq === e.seq) item.details.replaceChildren(...buildDetails(e));
  }
  setVerify(
    "ask",
    "edit",
    h("b", null, `Запись #${e.seq} изменена в памяти: ${before} → ${after}. `),
    "Хеш оставили прежним, как сделал бы злоумышленник. Нажмите «Проверить цепочку».",
  );
  renderJournal();
}

// ------------------------------------------------------------ общий цикл

function renderAll() {
  renderClock();
  renderPortfolio();
  renderAgentStatus();
  renderMetrics();
  renderJournal();
  renderMandateStatus();
  renderSeal();
  if (S.draft && S.draft.compiled) renderSignRow();
}

function resetDemo() {
  runToken += 1;
  S.gw = new Gateway({ market: MARKET });
  S.signed = null;
  S.signing = false;
  S.running = false;
  S.busy = false;
  S.activeScenario = null;
  S.done.clear();
  S.cardsByPending.clear();
  S.journalRendered = 0;
  S.journalItems.clear();
  S.openSeq = null;
  S.tamperedSeq = null;
  S.brokenSeq = null;
  S.posItems.clear();
  S.lastQty = null;
  S.lastCash = null;
  $("pf-positions").replaceChildren();
  $("feed").replaceChildren($("feed-empty"));
  $("feed-empty").hidden = false;
  $("j-list").replaceChildren();
  $("toasts").replaceChildren();
  $("verify-status").hidden = true;
  $("narrator").hidden = true;
  $("narrator").classList.remove("is-floating");
  $("feed-scroll").scrollTop = 0;
  $("journal-scroll").scrollTop = 0;
  $("mandate-scroll").scrollTop = 0;
  selectTemplate("t1");
  renderAll();
  return runToken;
}

function setupStatic() {
  setButton($("btn-compile"), null, "Собрать правила");
  setButton($("btn-reset"), "reset", "Сбросить демо");
  setButton($("btn-resume"), "play", "Снять паузу");
  setButton($("btn-verify"), "link", "Проверить цепочку");
  setButton($("btn-tamper"), "edit", "Подделать запись");
  $("feed-empty").querySelector(".empty-icon").append(icon("bot"));

  $("btn-compile").addEventListener("click", compile);
  $("btn-sign").addEventListener("click", sign);
  $("btn-run-all").addEventListener("click", runAll);
  $("btn-reset").addEventListener("click", () => resetDemo());
  $("btn-resume").addEventListener("click", onResume);
  $("btn-kill").addEventListener("click", onKill);
  $("btn-verify").addEventListener("click", doVerify);
  $("btn-tamper").addEventListener("click", doTamper);
  $("client-text").addEventListener("input", onTextInput);

  const choice = $("tpl-choice");
  const keys = ["t1", "t2", "own"];
  choice.addEventListener("click", (ev) => {
    const b = ev.target.closest(".choice-btn");
    if (b) selectTemplate(b.dataset.key);
  });
  choice.addEventListener("keydown", (ev) => {
    const dir = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[
      ev.key
    ];
    if (!dir) return;
    ev.preventDefault();
    const i = (keys.indexOf(S.draft.key) + dir + keys.length) % keys.length;
    selectTemplate(keys[i]);
    choice.querySelector(`[data-key="${keys[i]}"]`).focus();
  });
}

function init() {
  setupTheme();
  setupStatic();
  buildScenarioButtons();
  resetDemo();
}

if (document.readyState === "loading")
  document.addEventListener("DOMContentLoaded", init);
else init();
