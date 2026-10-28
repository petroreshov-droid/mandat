// Мандат - движок проверок, журнал и шлюз.
// ES-модуль без зависимостей: работает в браузере и в Node 18+.
// Источник истины - SPEC.md в корне проекта. Решения и коды проверок
// обязаны совпадать с Python-версией на одних и тех же входах.

export const SCHEMA = "mandat/v1";
export const GENESIS_HASH = "0".repeat(64);

export const CHECK_ORDER = [
  "KILL",
  "PAUSED",
  "DUPLICATE",
  "RUNAWAY",
  "INSTRUMENT",
  "TRADING_STATUS",
  "CLASS",
  "QUAL_ONLY",
  "CURRENCY",
  "LOT",
  "SIDE",
  "PRICE_DEVIATION",
  "FUNDS",
  "WHITELIST",
  "POSITION_SHARE",
  "ORDER_VALUE",
  "DAILY_TURNOVER",
  "ASK_THRESHOLD",
  "BOOK_DEPTH",
];

// Короткие подписи проверок для интерфейса.
export const CHECK_LABELS = {
  KILL: "Выключатель",
  PAUSED: "Пауза",
  DUPLICATE: "Повтор заявки",
  RUNAWAY: "Частота заявок",
  INSTRUMENT: "Бумага найдена",
  TRADING_STATUS: "Торги идут",
  CLASS: "Класс бумаги",
  QUAL_ONLY: "Для неквалов",
  CURRENCY: "Валюта",
  LOT: "Кратность лоту",
  SIDE: "Направление",
  PRICE_DEVIATION: "Цена у рынка",
  FUNDS: "Хватает денег",
  WHITELIST: "Ваш список",
  POSITION_SHARE: "Доля в портфеле",
  ORDER_VALUE: "Лимит заявки",
  DAILY_TURNOVER: "Дневной лимит",
  ASK_THRESHOLD: "Порог подписи",
  BOOK_DEPTH: "Глубина стакана",
  MANDATE_CHANGE: "Смена мандата",
};

export const CLASS_RU = {
  share: "акции",
  bond: "облигации",
  etf: "фонды",
  futures: "фьючерсы",
};
const CUR_PREP = { rub: "рублях", cny: "юанях", usd: "долларах", eur: "евро" };
const CUR_GEN = { rub: "рублей", cny: "юаней", usd: "долларов", eur: "евро" };

const MANDATE_CHANGE_DETAIL =
  "Мандат меняет только клиент через подпись новой версии.";
const ALLOW_TEXT = "В рамках мандата: заявка отправлена брокеру.";
const CONFIRM_TEXT =
  "Подтверждено вами: жёсткие правила перепроверены на свежих данных, заявка отправлена брокеру.";

// ---------------------------------------------------------------- форматирование

const NBSP = " ";

export function fmtNumber(v, maxDigits = 2, minDigits = 0) {
  if (v === null || v === undefined || !Number.isFinite(v)) return "-";
  const neg = v < 0;
  let [int, frac = ""] = Math.abs(v).toFixed(maxDigits).split(".");
  frac = frac.replace(/0+$/, "");
  while (frac.length < minDigits) frac += "0";
  int = int.replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
  return (neg ? "-" : "") + int + (frac ? "," + frac : "");
}

export function fmtMoney(v) {
  if (v === null || v === undefined || !Number.isFinite(v)) return "-";
  const whole = Math.abs(v - Math.round(v)) < 0.005;
  return (
    (whole ? fmtNumber(Math.round(v), 0) : fmtNumber(v, 2, 2)) + NBSP + "₽"
  );
}

export function fmtPct(fraction, digits = 1) {
  return fmtNumber(fraction * 100, digits);
}

function pctPair(a, b) {
  let x = fmtPct(a, 1);
  let y = fmtPct(b, 1);
  if (x === y) {
    x = fmtPct(a, 2);
    y = fmtPct(b, 2);
  }
  return [x, y];
}

export function plural(n, one, few, many) {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  if (b === 1) return one;
  return many;
}

export function joinRu(items) {
  if (items.length <= 1) return items.join("");
  return items.slice(0, -1).join(", ") + " и " + items[items.length - 1];
}

// ---------------------------------------------------------------- утилиты

export function clone(v) {
  return v === undefined ? undefined : JSON.parse(JSON.stringify(v));
}

const round2 = (v) => Math.round(v * 100) / 100;

function has(obj, key) {
  return (
    obj !== null &&
    obj !== undefined &&
    Object.prototype.hasOwnProperty.call(obj, key)
  );
}

// ---------------------------------------------------------------- канонический JSON и SHA-256

// Ключи отсортированы, разделители без пробелов, не-ASCII без экранирования
// (как json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)).
export function canonicalJSON(value) {
  if (value === null || value === undefined) return "null";
  switch (typeof value) {
    case "number":
      if (!Number.isFinite(value))
        throw new Error("canonicalJSON: число должно быть конечным");
      return JSON.stringify(value);
    case "boolean":
      return value ? "true" : "false";
    case "string":
      return JSON.stringify(value);
    case "object": {
      if (Array.isArray(value))
        return "[" + value.map((x) => canonicalJSON(x)).join(",") + "]";
      const keys = Object.keys(value)
        .filter((k) => value[k] !== undefined)
        .sort();
      return (
        "{" +
        keys
          .map((k) => JSON.stringify(k) + ":" + canonicalJSON(value[k]))
          .join(",") +
        "}"
      );
    }
    default:
      throw new Error("canonicalJSON: неподдерживаемый тип " + typeof value);
  }
}

function toHex(bytes) {
  let s = "";
  for (const b of bytes) s += b.toString(16).padStart(2, "0");
  return s;
}

// Запасной SHA-256 на чистом JS: нужен там, где нет crypto.subtle
// (страница открыта по http с чужого адреса, старый Node без глобального crypto).
let SHA_K = null;
let SHA_H = null;
function shaConstants() {
  if (SHA_K) return;
  const primes = [];
  for (let n = 2; primes.length < 64; n++) {
    if (primes.every((p) => n % p !== 0)) primes.push(n);
  }
  const frac = (x) => ((x - Math.floor(x)) * 4294967296) >>> 0;
  SHA_K = new Uint32Array(primes.map((p) => frac(Math.cbrt(p))));
  SHA_H = new Uint32Array(primes.slice(0, 8).map((p) => frac(Math.sqrt(p))));
}

export function sha256Fallback(bytes) {
  shaConstants();
  const len = bytes.length;
  const total = ((len + 9 + 63) >> 6) << 6;
  const buf = new Uint8Array(total);
  buf.set(bytes);
  buf[len] = 0x80;
  const dv = new DataView(buf.buffer);
  const bits = len * 8;
  dv.setUint32(total - 4, bits >>> 0);
  dv.setUint32(total - 8, Math.floor(bits / 4294967296));
  const h = Uint32Array.from(SHA_H);
  const w = new Uint32Array(64);
  const rotr = (x, n) => (x >>> n) | (x << (32 - n));
  for (let off = 0; off < total; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = dv.getUint32(off + i * 4);
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
    }
    let [a, b, c, d, e, f, g, hh] = h;
    for (let i = 0; i < 64; i++) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const t1 = (hh + S1 + ch + SHA_K[i] + w[i]) | 0;
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) | 0;
      hh = g;
      g = f;
      f = e;
      e = (d + t1) | 0;
      d = c;
      c = b;
      b = a;
      a = (t1 + t2) | 0;
    }
    h[0] += a;
    h[1] += b;
    h[2] += c;
    h[3] += d;
    h[4] += e;
    h[5] += f;
    h[6] += g;
    h[7] += hh;
  }
  return Array.from(h, (x) => x.toString(16).padStart(8, "0")).join("");
}

export async function sha256Hex(text) {
  const bytes = new TextEncoder().encode(text);
  const subtle = globalThis.crypto && globalThis.crypto.subtle;
  if (subtle)
    return toHex(new Uint8Array(await subtle.digest("SHA-256", bytes)));
  return sha256Fallback(bytes);
}

// ---------------------------------------------------------------- движок (SPEC 1-2)

export function normalizeOrder(order) {
  return {
    order_id: order.order_id ?? null,
    ticker: order.ticker,
    side: order.side,
    quantity: order.quantity,
    price: order.price ?? null,
    agent: order.agent ?? "agent",
    reason: order.reason ?? "",
  };
}

// Снимок рынка, нужный для проверки одной заявки. Хранится в журнале,
// по нему replay пересчитывает решение без доступа к брокеру.
export function takeSnapshot(order, market) {
  const instruments = market.instruments || {};
  const fxTable = market.fx || {};
  const positions = market.positions || {};
  let sumPositions = 0;
  for (const [t, qty] of Object.entries(positions)) {
    const it = instruments[t];
    if (!it) continue;
    sumPositions += qty * it.last * fxTable[it.currency];
  }
  const cash = market.cash_rub;
  const ticker = order.ticker;
  const inst = has(instruments, ticker) ? instruments[ticker] : null;
  const fx = inst ? fxTable[inst.currency] : null;
  const qty = inst ? (positions[ticker] ?? 0) : 0;
  const book =
    inst && market.books && has(market.books, ticker)
      ? clone(market.books[ticker])
      : null;
  return {
    ticker,
    cash_rub: cash,
    total_value: cash + sumPositions,
    position_value: inst ? qty * inst.last * fx : 0,
    position_qty: qty,
    fx,
    instrument: inst ? { ticker, ...clone(inst) } : null,
    book,
  };
}

// Состояние шлюза в том виде, в каком его видит проверка (и пишет журнал).
export function stateView(order, state = {}) {
  const now = state.now ?? 0;
  const recent = (state.recent_order_ts || []).filter(
    (ts) => now - ts < 60,
  ).length;
  const seen =
    order.order_id !== null &&
    order.order_id !== undefined &&
    (state.seen_order_ids || []).includes(order.order_id);
  return {
    now,
    turnover_today: state.turnover_today ?? 0,
    orders_last_minute: recent,
    order_id_seen: seen,
    paused: !!state.paused,
    kill_switch: !!state.kill_switch,
  };
}

function decide(checks, actions, orderValue) {
  const hard = checks.find((c) => !c.ok && c.severity === "hard");
  const soft = checks.find((c) => !c.ok && c.severity === "soft");
  let decision;
  let explanation;
  if (hard) {
    decision = "DENY";
    explanation = `Отклонено: ${hard.detail}`;
  } else if (soft) {
    decision = "ASK";
    explanation = `Нужно ваше подтверждение: ${soft.detail}`;
  } else {
    decision = "ALLOW";
    explanation = ALLOW_TEXT;
  }
  return {
    decision,
    checks,
    explanation,
    order_value: round2(orderValue),
    actions,
  };
}

// Ядро: 19 проверок строго по порядку SPEC 2.
export function evaluateCore(order, mandate, snap, sv) {
  const L = mandate.limits || {};
  const checks = [];
  const actions = [];
  const inst = snap.instrument;
  const price = order.price ?? null;
  const q = order.quantity;
  const execPrice = inst ? (price !== null ? price : inst.last) : null;
  const orderValue = inst ? q * execPrice * snap.fx : 0;
  const push = (code, ok, severity, detail) => {
    checks.push({ code, ok, severity, detail });
    return ok;
  };
  const stop = () => decide(checks, actions, orderValue);

  // 1-5: при провале оценка прерывается
  if (
    !push(
      "KILL",
      !sv.kill_switch,
      "hard",
      sv.kill_switch ? "Агент выключен клиентом." : "Агент включён.",
    )
  )
    return stop();
  if (
    !push(
      "PAUSED",
      !sv.paused,
      "hard",
      sv.paused
        ? "Агент на паузе после серии нарушений. Снять паузу может только клиент."
        : "Паузы нет.",
    )
  )
    return stop();
  if (
    !push(
      "DUPLICATE",
      !sv.order_id_seen,
      "hard",
      sv.order_id_seen
        ? `Повтор заявки ${order.order_id}: она уже обработана, вторая не отправлена.`
        : "Заявка новая.",
    )
  )
    return stop();
  if (
    L.max_orders_per_minute !== null &&
    L.max_orders_per_minute !== undefined
  ) {
    const n = sv.orders_last_minute;
    const max = L.max_orders_per_minute;
    const runaway = n >= max;
    if (runaway) actions.push("PAUSE");
    const k = n + 1;
    if (
      !push(
        "RUNAWAY",
        !runaway,
        "hard",
        runaway
          ? `Похоже на зацикливание: ${k} ${plural(k, "заявка", "заявки", "заявок")} за минуту при лимите ${max}. Агент поставлен на паузу.`
          : `${k}-я заявка за минуту, лимит ${max}.`,
      )
    )
      return stop();
  }
  if (
    !push(
      "INSTRUMENT",
      !!inst,
      "hard",
      inst
        ? `${inst.name} (${inst.ticker}).`
        : `Бумага ${order.ticker} не найдена.`,
    )
  )
    return stop();

  const name = inst.name;
  const buy = order.side === "buy";
  const sell = order.side === "sell";

  // 6
  push(
    "TRADING_STATUS",
    inst.status === "normal",
    "hard",
    inst.status === "normal"
      ? "Торги идут."
      : `Торги по ${name} сейчас не идут.`,
  );
  // 7
  const classOk = (mandate.allowed_classes || []).includes(inst.class);
  push(
    "CLASS",
    classOk,
    "hard",
    classOk
      ? `Класс «${CLASS_RU[inst.class] || inst.class}» разрешён.`
      : `Класс «${CLASS_RU[inst.class] || inst.class}» запрещён мандатом.`,
  );
  // 8
  const qualBad = !!inst.qual_only && !!mandate.forbid_qual_only;
  push(
    "QUAL_ONLY",
    !qualBad,
    "hard",
    qualBad
      ? `${name} - только для квалифицированных инвесторов.`
      : inst.qual_only
        ? "Бумага для квалов, мандат это разрешает."
        : "Доступна всем инвесторам.",
  );
  // 9
  const curOk = (mandate.allowed_currencies || []).includes(inst.currency);
  push(
    "CURRENCY",
    curOk,
    "hard",
    curOk
      ? `Валюта ${String(inst.currency).toUpperCase()} разрешена.`
      : `Валюта ${String(inst.currency).toUpperCase()} не разрешена мандатом.`,
  );
  // 10
  const lotBad = q <= 0 || q % inst.lot !== 0;
  push(
    "LOT",
    !lotBad,
    "hard",
    lotBad
      ? `Количество ${fmtNumber(q, 0)} не кратно лоту ${fmtNumber(inst.lot, 0)}.`
      : `${fmtNumber(q / inst.lot, 2)} ${plural(q / inst.lot, "лот", "лота", "лотов")} по ${fmtNumber(inst.lot, 0)} шт.`,
  );
  // 11
  let sideOk = true;
  let sideDetail = buy ? "Покупка." : "Продажа из позиции.";
  if (sell && !mandate.allow_sell) {
    sideOk = false;
    sideDetail = "Продажи по мандату запрещены.";
  } else if (sell && mandate.forbid_leverage && q > snap.position_qty) {
    sideOk = false;
    sideDetail = "Продажа больше позиции - это шорт, запрещено.";
  }
  push("SIDE", sideOk, "hard", sideDetail);
  // 12
  if (L.max_price_deviation !== null && L.max_price_deviation !== undefined) {
    if (price === null) {
      push(
        "PRICE_DEVIATION",
        true,
        "hard",
        "Рыночная заявка: оценка по последней цене.",
      );
    } else {
      const dev = Math.abs(price - inst.last) / inst.last;
      const bad = dev > L.max_price_deviation;
      push(
        "PRICE_DEVIATION",
        !bad,
        "hard",
        bad
          ? `Цена ${fmtNumber(price, 4)} отличается от рынка (${fmtNumber(inst.last, 4)}) на ${fmtPct(dev, 2)}%, лимит ${fmtPct(L.max_price_deviation, 2)}%.`
          : `Отклонение от рынка ${fmtPct(dev, 2)}%, лимит ${fmtPct(L.max_price_deviation, 2)}%.`,
      );
    }
  }
  // 13
  const fundsBad =
    buy && !!mandate.forbid_leverage && orderValue > snap.cash_rub;
  let fundsDetail;
  if (fundsBad)
    fundsDetail = `Не хватает денег: нужно ${fmtMoney(orderValue)}, свободно ${fmtMoney(snap.cash_rub)}. Плечо запрещено.`;
  else if (!buy) fundsDetail = "Продажа: деньги не нужны.";
  else if (mandate.forbid_leverage)
    fundsDetail = `Нужно ${fmtMoney(orderValue)}, свободно ${fmtMoney(snap.cash_rub)}.`;
  else fundsDetail = "Плечо разрешено мандатом.";
  push("FUNDS", !fundsBad, "hard", fundsDetail);
  // 14
  const list = mandate.allowed_tickers || [];
  const wlBad = list.length > 0 && !list.includes(order.ticker);
  push(
    "WHITELIST",
    !wlBad,
    mandate.strict_whitelist ? "hard" : "soft",
    wlBad
      ? `Бумаги ${name} нет в вашем списке.`
      : list.length
        ? "Бумага из вашего списка."
        : "Списка нет: подходит любая бумага.",
  );
  // 15
  if (L.max_position_share !== null && L.max_position_share !== undefined) {
    if (!buy) {
      push("POSITION_SHARE", true, "hard", "Продажа: доля не растёт.");
    } else {
      const share = (snap.position_value + orderValue) / snap.total_value;
      const bad = share > L.max_position_share;
      const [s, m] = pctPair(share, L.max_position_share);
      push(
        "POSITION_SHARE",
        !bad,
        "hard",
        bad
          ? `После покупки доля ${name} станет ${s}%, лимит ${m}%.`
          : `Доля станет ${s}%, лимит ${m}%.`,
      );
    }
  }
  // 16
  if (L.max_order_value !== null && L.max_order_value !== undefined) {
    const bad = orderValue > L.max_order_value;
    push(
      "ORDER_VALUE",
      !bad,
      "hard",
      bad
        ? `Заявка на ${fmtMoney(orderValue)} больше лимита ${fmtMoney(L.max_order_value)}.`
        : `${fmtMoney(orderValue)} из ${fmtMoney(L.max_order_value)}.`,
    );
  }
  // 17
  if (L.daily_turnover !== null && L.daily_turnover !== undefined) {
    const bad = sv.turnover_today + orderValue > L.daily_turnover;
    push(
      "DAILY_TURNOVER",
      !bad,
      "hard",
      bad
        ? `Дневной лимит ${fmtMoney(L.daily_turnover)} будет превышен: уже ${fmtMoney(sv.turnover_today)}.`
        : `За день станет ${fmtMoney(sv.turnover_today + orderValue)} из ${fmtMoney(L.daily_turnover)}.`,
    );
  }
  // 18
  if (L.ask_above !== null && L.ask_above !== undefined) {
    const bad = orderValue > L.ask_above;
    push(
      "ASK_THRESHOLD",
      !bad,
      "soft",
      bad
        ? `Заявка на ${fmtMoney(orderValue)}: крупные заявки по мандату подтверждаете вы.`
        : `Меньше порога ${fmtMoney(L.ask_above)}.`,
    );
  }
  // 19
  if (L.max_book_share !== null && L.max_book_share !== undefined) {
    const levels = snap.book ? (buy ? snap.book.asks : snap.book.bids) : null;
    if (levels && levels.length) {
      const best = levels[0][0];
      let depth = 0;
      for (const [p, lots] of levels) {
        if (buy ? p <= best * 1.01 : p >= best * 0.99) depth += lots;
      }
      const orderLots = q / inst.lot;
      const bad = orderLots > L.max_book_share * depth;
      const pct = depth > 0 ? fmtNumber((orderLots / depth) * 100, 1) : "100";
      push(
        "BOOK_DEPTH",
        !bad,
        "soft",
        bad
          ? `Заявка забирает ${pct}% видимого стакана: возможен сдвиг цены.`
          : `${pct}% видимого стакана, лимит ${fmtPct(L.max_book_share, 0)}%.`,
      );
    }
  }
  return decide(checks, actions, orderValue);
}

// Публичный контракт (общий с Python): evaluate({order, mandate, market, state}).
export function evaluate({ order, mandate, market, state }) {
  const o = normalizeOrder(order);
  return evaluateCore(
    o,
    mandate,
    takeSnapshot(o, market),
    stateView(o, state || {}),
  );
}

// Подтверждение клиентом: только жёсткие проверки на свежем снимке.
function hardOnly(full) {
  const checks = full.checks.filter((c) => c.severity === "hard");
  const bad = checks.find((c) => !c.ok);
  return {
    decision: bad ? "DENY" : "ALLOW",
    checks,
    explanation: bad
      ? `Отклонено при подтверждении: ${bad.detail}`
      : CONFIRM_TEXT,
    order_value: full.order_value,
    actions: [],
  };
}

function mandateChangeResult() {
  return {
    decision: "DENY",
    checks: [
      {
        code: "MANDATE_CHANGE",
        ok: false,
        severity: "hard",
        detail: MANDATE_CHANGE_DETAIL,
      },
    ],
    explanation: `Отклонено: ${MANDATE_CHANGE_DETAIL}`,
    order_value: 0,
    actions: [],
  };
}

// ---------------------------------------------------------------- стресс-тест (SPEC 5)

export function lint(m) {
  const L = m.limits || {};
  const out = [];
  const add = (code, level, text, fix = null) =>
    out.push({ code, level, text, fix });
  const isNull = (v) => v === null || v === undefined;
  if (isNull(L.daily_turnover)) {
    add(
      "L_NO_DAILY",
      "warn",
      "Нет дневного лимита: агент сможет потратить весь кэш за один день.",
      {
        label: `Лимит ${fmtMoney(100000)} в день`,
        set: { "limits.daily_turnover": 100000 },
      },
    );
  }
  if (isNull(L.max_order_value)) {
    add("L_NO_ORDER_MAX", "warn", "Нет лимита на одну заявку.", {
      label: `Не больше ${fmtMoney(50000)} на заявку`,
      set: { "limits.max_order_value": 50000 },
    });
  }
  if (isNull(L.max_orders_per_minute)) {
    add(
      "L_NO_RUNAWAY",
      "critical",
      "Нет защиты от зацикливания: робот сможет отправить сотни заявок.",
      {
        label: "Не больше 5 заявок в минуту",
        set: { "limits.max_orders_per_minute": 5 },
      },
    );
  }
  if (!(m.allowed_tickers || []).length) {
    add(
      "L_NO_WHITELIST",
      "warn",
      "Нет списка бумаг: агент сможет купить любую бумагу разрешённых классов.",
    );
  }
  if (m.allow_sell) {
    add("L_SELL_OPEN", "warn", "Продажи разрешены без условий.", {
      label: "Запретить продажи",
      set: { allow_sell: false },
    });
  }
  if (!m.forbid_leverage) {
    add(
      "L_LEVERAGE",
      "critical",
      "Плечо разрешено: убыток может превысить вложенное.",
      { label: "Запретить плечо", set: { forbid_leverage: true } },
    );
  }
  if ((m.allowed_classes || []).includes("futures")) {
    add(
      "L_DERIVATIVES",
      "critical",
      "Разрешены фьючерсы: это сделки с плечом.",
      {
        label: "Убрать фьючерсы",
        set: {
          allowed_classes: m.allowed_classes.filter((c) => c !== "futures"),
        },
      },
    );
  }
  if (isNull(L.max_position_share)) {
    add(
      "L_NO_SHARE",
      "warn",
      "Нет лимита доли: портфель может оказаться в одной бумаге.",
      {
        label: "Не больше 15% в одной бумаге",
        set: { "limits.max_position_share": 0.15 },
      },
    );
  } else if (L.max_position_share > 0.5) {
    add("L_SHARE_HIGH", "warn", "Лимит доли выше 50%.", {
      label: "Снизить до 15%",
      set: { "limits.max_position_share": 0.15 },
    });
  }
  for (const so of m.standing_orders || []) {
    const amount = so.amount_rub;
    if (isNull(amount)) continue;
    const overOrder = !isNull(L.max_order_value) && amount > L.max_order_value;
    const overDay = !isNull(L.daily_turnover) && amount > L.daily_turnover;
    if (overOrder || overDay) {
      add(
        "L_STANDING_CONFLICT",
        "warn",
        `Поручение «${so.id}» больше лимита: оно не исполнится.`,
      );
    }
  }
  return out;
}

export function setPath(obj, path, value) {
  const parts = path.split(".");
  let cur = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    if (cur[parts[i]] === null || typeof cur[parts[i]] !== "object")
      cur[parts[i]] = {};
    cur = cur[parts[i]];
  }
  cur[parts[parts.length - 1]] = clone(value);
}

export function getPath(obj, path) {
  return path
    .split(".")
    .reduce(
      (cur, k) => (cur === null || cur === undefined ? undefined : cur[k]),
      obj,
    );
}

export function applyFix(mandate, fix) {
  const m = clone(mandate);
  for (const [path, value] of Object.entries(fix.set || {}))
    setPath(m, path, value);
  return m;
}

// ---------------------------------------------------------------- правила простыми словами

export function describeMandate(m, market = null) {
  const L = m.limits || {};
  const inst = (market && market.instruments) || {};
  const nameOf = (t) =>
    inst[t] && inst[t].name.length <= 18 ? inst[t].name : t;
  const classes = m.allowed_classes || [];
  const classList = joinRu(classes.map((c) => CLASS_RU[c] || c));
  const tickers = m.allowed_tickers || [];
  const can = [];
  const cannot = [];
  const ask = [];
  const notNull = (v) => v !== null && v !== undefined;

  if (tickers.length)
    can.push(
      `Покупать ${classList} из вашего списка: ${tickers.map(nameOf).join(", ")}.`,
    );
  else if (classes.length)
    can.push(`Покупать любые ${classList}: списка бумаг нет.`);
  for (const so of m.standing_orders || []) {
    if (so.on === "coupon") {
      can.push(
        `Реинвестировать купоны в ту из бумаг ${joinRu((so.from || []).map(nameOf))}, где ваша доля меньше.`,
      );
    } else if (so.on === "monthly") {
      can.push(
        `Каждое ${so.day}-е число покупать ${nameOf(so.ticker)} на ${fmtMoney(so.amount_rub)}.`,
      );
    }
  }
  if (m.allow_sell)
    can.push(
      m.forbid_leverage
        ? "Продавать бумаги из портфеля."
        : "Продавать бумаги, в том числе в шорт.",
    );
  if (!m.forbid_leverage)
    can.push("Брать плечо: покупать больше, чем есть свободных денег.");
  if (!m.forbid_qual_only)
    can.push("Покупать бумаги для квалифицированных инвесторов.");
  if (notNull(L.ask_above))
    can.push(`Исполнять заявки до ${fmtMoney(L.ask_above)} без вас.`);
  else can.push("Исполнять заявки любого размера без вас.");

  if (!m.allow_sell) cannot.push("Продавать бумаги.");
  if (m.forbid_leverage)
    cannot.push(
      m.allow_sell
        ? "Брать плечо и продавать в шорт: только свободные деньги и бумаги из портфеля."
        : "Брать плечо: покупки только на свободные деньги.",
    );
  const banned = ["share", "bond", "etf", "futures"].filter(
    (c) => !classes.includes(c),
  );
  if (banned.length)
    cannot.push(`Покупать ${joinRu(banned.map((c) => CLASS_RU[c]))}.`);
  if (m.forbid_qual_only)
    cannot.push("Покупать бумаги только для квалифицированных инвесторов.");
  const known = market && market.fx ? Object.keys(market.fx) : ["rub", "cny"];
  const curs = m.allowed_currencies || [];
  const bannedCur = known.filter((c) => !curs.includes(c));
  if (bannedCur.length)
    cannot.push(
      `Покупать бумаги в ${joinRu(bannedCur.map((c) => CUR_PREP[c] || c.toUpperCase()))}.`,
    );
  if (notNull(L.max_position_share))
    cannot.push(
      `Держать в одной бумаге больше ${fmtPct(L.max_position_share, 1)}% портфеля.`,
    );
  if (notNull(L.max_order_value))
    cannot.push(`Отправить заявку больше ${fmtMoney(L.max_order_value)}.`);
  if (notNull(L.daily_turnover))
    cannot.push(`Потратить за день больше ${fmtMoney(L.daily_turnover)}.`);
  if (notNull(L.max_price_deviation))
    cannot.push(
      `Ставить цену дальше ${fmtPct(L.max_price_deviation, 2)}% от рынка.`,
    );
  if (notNull(L.max_orders_per_minute)) {
    const n = L.max_orders_per_minute;
    cannot.push(
      `Отправлять больше ${n} ${plural(n, "заявки", "заявок", "заявок")} в минуту: при зацикливании шлюз ставит паузу.`,
    );
  }
  if (tickers.length && m.strict_whitelist)
    cannot.push("Покупать бумаги не из вашего списка.");
  cannot.push("Менять мандат: это делаете только вы.");

  if (notNull(L.ask_above))
    ask.push(`Перед заявкой больше ${fmtMoney(L.ask_above)}.`);
  if (tickers.length && !m.strict_whitelist)
    ask.push("Перед покупкой бумаги не из вашего списка.");
  if (notNull(L.max_book_share))
    ask.push(
      `Если заявка забирает больше ${fmtPct(L.max_book_share, 0)}% видимого стакана.`,
    );
  return { can, cannot, ask };
}

// ---------------------------------------------------------------- журнал (SPEC 4)

export class Journal {
  constructor() {
    this.entries = [];
    this._tail = Promise.resolve();
  }

  get length() {
    return this.entries.length;
  }

  get last() {
    return this.entries.length ? this.entries[this.entries.length - 1] : null;
  }

  // Записи добавляются строго по очереди: хеш следующей зависит от предыдущей.
  append(type, payload, ts) {
    const run = this._tail.then(async () => {
      const seq = this.entries.length + 1;
      const prev = this.last;
      const body = {
        seq,
        ts,
        type,
        payload: clone(payload),
        prev_hash: prev ? prev.hash : GENESIS_HASH,
      };
      const hash = await sha256Hex(canonicalJSON(body));
      const entry = { ...body, hash };
      this.entries.push(entry);
      return entry;
    });
    this._tail = run.catch(() => {});
    return run;
  }

  get(seq) {
    return this.entries[seq - 1] || null;
  }

  // Номер первой битой записи или null.
  async verify() {
    let prevHash = GENESIS_HASH;
    for (let i = 0; i < this.entries.length; i++) {
      const e = this.entries[i];
      if (e.seq !== i + 1 || e.prev_hash !== prevHash) return e.seq ?? i + 1;
      const body = {
        seq: e.seq,
        ts: e.ts,
        type: e.type,
        payload: e.payload,
        prev_hash: e.prev_hash,
      };
      if ((await sha256Hex(canonicalJSON(body))) !== e.hash) return e.seq;
      prevHash = e.hash;
    }
    return null;
  }

  mandateByHash(hash) {
    const e = this.entries.find(
      (x) =>
        x.type === "mandate_signed" &&
        x.payload &&
        x.payload.mandate_hash === hash,
    );
    return e ? e.payload.mandate : null;
  }

  // Повторный прогон движка на сохранённых в записи входах.
  async replay(seq) {
    const e = this.get(seq);
    if (!e) return { seq, supported: false, reason: "Записи нет." };
    if (e.type !== "decision" && e.type !== "confirm") {
      return {
        seq,
        supported: false,
        reason: "Replay работает для решений шлюза.",
      };
    }
    const p = e.payload;
    const mandate = this.mandateByHash(p.mandate_hash);
    if (!mandate)
      return {
        seq,
        supported: true,
        match: false,
        reason: "Мандат с таким хешем не найден.",
      };
    const mandateOk =
      (await sha256Hex(canonicalJSON(mandate))) === p.mandate_hash;
    let res;
    if (!p.order) {
      res = mandateChangeResult();
    } else {
      const full = evaluateCore(p.order, mandate, p.snapshot, {
        now: e.ts,
        ...p.state_before,
      });
      res = e.type === "confirm" ? hardOnly(full) : full;
    }
    const sig = (checks) =>
      (checks || []).map((c) => [c.code, c.ok, c.severity]);
    const stored = { decision: p.decision, checks: sig(p.checks) };
    const replayed = { decision: res.decision, checks: sig(res.checks) };
    const sameChecks =
      JSON.stringify(stored.checks) === JSON.stringify(replayed.checks);
    const sameDecision = stored.decision === replayed.decision;
    return {
      seq,
      supported: true,
      match: sameDecision && sameChecks && mandateOk,
      sameDecision,
      sameChecks,
      mandateOk,
      stored,
      replayed,
      explanation: res.explanation,
    };
  }
}

// ---------------------------------------------------------------- mock-брокер

export class MockBroker {
  constructor(market) {
    this.market = clone(market);
    this.fills = [];
  }

  snapshot() {
    return clone(this.market);
  }

  credit(amountRub) {
    this.market.cash_rub = round2(this.market.cash_rub + amountRub);
  }

  // Исполнение по exec_price, без комиссии и без влияния на стакан.
  execute(order) {
    const m = this.market;
    const inst = m.instruments[order.ticker];
    const price = order.price !== null ? order.price : inst.last;
    const value = round2(order.quantity * price * m.fx[inst.currency]);
    const pos = m.positions[order.ticker] || 0;
    if (order.side === "buy") {
      m.cash_rub = round2(m.cash_rub - value);
      m.positions[order.ticker] = pos + order.quantity;
    } else {
      m.cash_rub = round2(m.cash_rub + value);
      const left = pos - order.quantity;
      if (left === 0) delete m.positions[order.ticker];
      else m.positions[order.ticker] = left;
    }
    const fill = {
      id: "B-" + String(this.fills.length + 1).padStart(5, "0"),
      ticker: order.ticker,
      side: order.side,
      quantity: order.quantity,
      price,
      value,
    };
    this.fills.push(fill);
    return fill;
  }
}

// ---------------------------------------------------------------- шлюз (SPEC 3, 6)

export function parseTs(iso) {
  return Date.parse(iso) / 1000;
}

export function parseTzOffset(iso) {
  const m = /([+-])(\d{2}):?(\d{2})$/.exec(String(iso || ""));
  if (!m) return 0;
  return (m[1] === "-" ? -1 : 1) * (Number(m[2]) * 3600 + Number(m[3]) * 60);
}

export class Gateway {
  constructor({ market, journal = null, clockStart = null } = {}) {
    this.market0 = clone(market);
    this.broker = new MockBroker(market);
    this.asOf = parseTs(market.as_of);
    this.tzOffset = parseTzOffset(market.as_of);
    this.now = clockStart ?? this.asOf - 300; // подпись мандата за 5 минут до первого сценария
    this.journal = journal || new Journal();
    this.mandate = null;
    this.mandateHash = null;
    this.state = {
      turnover_today: 0,
      turnover_day: this.dayKey(this.now),
      recent_order_ts: [],
      seen_order_ids: [],
      violation_ts: [],
      paused: false,
      kill_switch: false,
    };
    this.pending = new Map();
    this.stats = {
      attempts: 0,
      orderAttempts: 0,
      executed: 0,
      denied: 0,
      rejected: 0,
      wouldViolate: 0,
      slipped: 0,
    };
    this.scenarioCount = 0;
    this._oid = 0;
    this._pid = 0;
  }

  // -------- часы
  dayKey(ts) {
    return Math.floor((ts + this.tzOffset) / 86400);
  }

  advanceTo(ts) {
    if (ts > this.now) this.now = ts;
    this._rollDay();
    return this.now;
  }

  _rollDay() {
    const d = this.dayKey(this.now);
    if (d !== this.state.turnover_day) {
      this.state.turnover_day = d;
      this.state.turnover_today = 0;
    }
  }

  nextScenarioSlot() {
    const slot = this.asOf + 600 * this.scenarioCount;
    this.scenarioCount += 1;
    return Math.max(slot, this.now);
  }

  newOrderId() {
    this._oid += 1;
    return "ord-" + String(this._oid).padStart(4, "0");
  }

  get status() {
    if (!this.mandate) return "idle";
    if (this.state.kill_switch) return "killed";
    if (this.state.paused) return "paused";
    return "active";
  }

  _requireMandate() {
    if (!this.mandate) throw new Error("Мандат не подписан.");
  }

  _engineState() {
    return {
      now: this.now,
      turnover_today: this.state.turnover_today,
      recent_order_ts: this.state.recent_order_ts.slice(),
      seen_order_ids: this.state.seen_order_ids.slice(),
      violation_ts: this.state.violation_ts.slice(),
      paused: this.state.paused,
      kill_switch: this.state.kill_switch,
    };
  }

  _stateBefore(sv) {
    return {
      turnover_today: sv.turnover_today,
      orders_last_minute: sv.orders_last_minute,
      paused: sv.paused,
      kill_switch: sv.kill_switch,
      order_id_seen: sv.order_id_seen,
    };
  }

  // Три жёстких отказа за 10 минут или зацикливание -> пауза.
  _registerDeny(now, actions) {
    this.state.violation_ts.push(now);
    this.state.violation_ts = this.state.violation_ts.filter(
      (ts) => now - ts < 600,
    );
    const strikes = this.state.violation_ts.length;
    if (!this.state.paused && (strikes >= 3 || actions.includes("PAUSE"))) {
      this.state.paused = true;
      return {
        pausedNow: true,
        reason: actions.includes("PAUSE") ? "runaway" : "strikes",
        strikes,
      };
    }
    return { pausedNow: false, strikes };
  }

  // -------- инструменты агента
  getPortfolio() {
    const m = this.broker.market;
    const rows = [];
    let sum = 0;
    for (const [t, qty] of Object.entries(m.positions)) {
      const it = m.instruments[t];
      if (!it) continue;
      const value = qty * it.last * m.fx[it.currency];
      sum += value;
      rows.push({ ticker: t, name: it.name, class: it.class, qty, value });
    }
    const total = m.cash_rub + sum;
    for (const r of rows) r.share = total ? r.value / total : 0;
    return { cash_rub: m.cash_rub, total_value: total, positions: rows };
  }

  getInstrument(ticker) {
    const it = this.broker.market.instruments[ticker];
    return it ? { ticker, ...clone(it) } : null;
  }

  getOrderbook(ticker) {
    return clone(this.broker.market.books[ticker] || null);
  }

  getMandate() {
    this._requireMandate();
    return {
      mandate_hash: this.mandateHash,
      rules: describeMandate(this.mandate, this.broker.market),
    };
  }

  async postOrder(order) {
    this._requireMandate();
    this._rollDay();
    const o = normalizeOrder(order);
    const now = this.now;
    const snap = takeSnapshot(o, this.broker.snapshot());
    const sv = stateView(o, this._engineState());
    const res = evaluateCore(o, this.mandate, snap, sv);

    this.state.recent_order_ts.push(now);
    this.state.recent_order_ts = this.state.recent_order_ts.filter(
      (ts) => now - ts < 60,
    );
    if (o.order_id !== null) this.state.seen_order_ids.push(o.order_id);
    this.stats.attempts += 1;
    this.stats.orderAttempts += 1;
    if (res.decision !== "ALLOW") this.stats.wouldViolate += 1;

    let pause = { pausedNow: false };
    let broker = null;
    let pendingId = null;
    if (res.decision === "DENY") {
      this.stats.denied += 1;
      pause = this._registerDeny(now, res.actions);
    } else if (res.decision === "ALLOW") {
      const fill = this.broker.execute(o);
      broker = { order_id: fill.id, status: "filled" };
      this.state.turnover_today = round2(
        this.state.turnover_today + res.order_value,
      );
      this.stats.executed += 1;
      if (res.checks.some((c) => !c.ok)) this.stats.slipped += 1; // по построению всегда 0
    } else {
      this._pid += 1;
      pendingId = "P-" + String(this._pid).padStart(3, "0");
      this.pending.set(pendingId, {
        id: pendingId,
        order: o,
        created: now,
        seq: null,
        order_value: res.order_value,
      });
    }

    const entry = await this.journal.append(
      "decision",
      {
        order: o,
        mandate_hash: this.mandateHash,
        snapshot: snap,
        state_before: this._stateBefore(sv),
        checks: res.checks,
        decision: res.decision,
        explanation: res.explanation,
        order_value: res.order_value,
        actions: res.actions,
        broker,
        agent: o.agent,
        pending_id: pendingId,
      },
      now,
    );
    if (pendingId && this.pending.has(pendingId))
      this.pending.get(pendingId).seq = entry.seq;
    return {
      ...res,
      kind: "order",
      order: o,
      snapshot: snap,
      entry,
      broker,
      pendingId,
      pausedNow: pause.pausedNow,
      pauseReason: pause.reason || null,
      paused: this.state.paused,
    };
  }

  async updateMandate(changes, { agent = "agent", reason = "" } = {}) {
    this._requireMandate();
    this._rollDay();
    const now = this.now;
    const res = mandateChangeResult();
    const sv = {
      turnover_today: this.state.turnover_today,
      orders_last_minute: this.state.recent_order_ts.filter(
        (ts) => now - ts < 60,
      ).length,
      paused: this.state.paused,
      kill_switch: this.state.kill_switch,
      order_id_seen: false,
    };
    this.stats.attempts += 1;
    this.stats.denied += 1;
    const pause = this._registerDeny(now, []);
    const entry = await this.journal.append(
      "decision",
      {
        order: null,
        request: {
          tool: "update_mandate",
          changes: clone(changes),
          agent,
          reason,
        },
        mandate_hash: this.mandateHash,
        snapshot: null,
        state_before: sv,
        checks: res.checks,
        decision: res.decision,
        explanation: res.explanation,
        order_value: 0,
        actions: [],
        broker: null,
        agent,
        pending_id: null,
      },
      now,
    );
    return {
      ...res,
      kind: "update_mandate",
      changes: clone(changes),
      request: entry.payload.request,
      entry,
      pausedNow: pause.pausedNow,
      pauseReason: pause.reason || null,
      paused: this.state.paused,
    };
  }

  // -------- поручения (SPEC 6)
  planCoupon(so, amount) {
    const m = this.broker.market;
    const pf = this.getPortfolio();
    const shareOf = (t) => {
      const row = pf.positions.find((r) => r.ticker === t);
      return row ? row.share : 0;
    };
    const candidates = (so.from || []).filter((t) => m.instruments[t]);
    if (!candidates.length) return null;
    let best = candidates[0];
    for (const t of candidates) if (shareOf(t) < shareOf(best)) best = t;
    const it = m.instruments[best];
    const book = m.books[best];
    const price =
      book && book.asks && book.asks.length ? book.asks[0][0] : it.last;
    const qty =
      Math.floor(amount / (price * m.fx[it.currency]) / it.lot) * it.lot;
    if (qty <= 0) return null;
    return { ticker: best, side: "buy", quantity: qty, price };
  }

  async coupon(amount, { agent = "agent" } = {}) {
    this._requireMandate();
    this._rollDay();
    this.broker.credit(amount);
    const so = (this.mandate.standing_orders || []).find(
      (s) => s.on === "coupon",
    );
    if (!so)
      return {
        amount,
        standing: null,
        order: null,
        outcome: null,
        cash_after: this.broker.market.cash_rub,
      };
    const plan = this.planCoupon(so, amount);
    if (!plan)
      return {
        amount,
        standing: so,
        order: null,
        outcome: null,
        cash_after: this.broker.market.cash_rub,
      };
    const order = {
      ...plan,
      order_id: this.newOrderId(),
      agent,
      reason: `Поручение «${so.id}»: купон ${fmtMoney(amount)} - в бумагу с наименьшей долей.`,
    };
    const outcome = await this.postOrder(order);
    return {
      amount,
      standing: so,
      order: outcome.order,
      outcome,
      cash_after: this.broker.market.cash_rub,
    };
  }

  dayOfMonth() {
    return new Date((this.now + this.tzOffset) * 1000).getUTCDate();
  }

  async monthly({ agent = "agent" } = {}) {
    this._requireMandate();
    this._rollDay();
    const day = this.dayOfMonth();
    const list = (this.mandate.standing_orders || []).filter(
      (s) => s.on === "monthly" && s.day === day,
    );
    const out = [];
    for (const so of list) {
      const m = this.broker.market;
      const it = m.instruments[so.ticker];
      if (!it) continue;
      const qty =
        Math.floor(so.amount_rub / (it.last * m.fx[it.currency]) / it.lot) *
        it.lot;
      if (qty <= 0) continue;
      const outcome = await this.postOrder({
        ticker: so.ticker,
        side: "buy",
        quantity: qty,
        price: null,
        order_id: this.newOrderId(),
        agent,
        reason: `Поручение «${so.id}»: ${so.day}-е число, ${so.ticker} на ${fmtMoney(so.amount_rub)}.`,
      });
      out.push({ standing: so, outcome });
    }
    return { day, items: out };
  }

  // -------- действия клиента
  async signMandate(mandate) {
    const findings = lint(mandate);
    if (findings.some((f) => f.level === "critical")) {
      throw new Error("Мандат с критичными дырами подписать нельзя.");
    }
    const m = clone(mandate);
    const hash = await sha256Hex(canonicalJSON(m));
    this.mandate = m;
    this.mandateHash = hash;
    const entry = await this.journal.append(
      "mandate_signed",
      {
        mandate: m,
        mandate_hash: hash,
        title: m.title,
        findings: findings.map((f) => f.code),
      },
      this.now,
    );
    return { hash, entry };
  }

  async confirm(pendingId) {
    const p = this.pending.get(pendingId);
    if (!p) throw new Error("Заявка не ждёт подтверждения.");
    this._rollDay();
    this.pending.delete(pendingId);
    const now = this.now;
    const snap = takeSnapshot(p.order, this.broker.snapshot());
    const sv = {
      now,
      turnover_today: this.state.turnover_today,
      orders_last_minute: 0, // подтверждение - действие клиента, не попытка агента
      order_id_seen: false,
      paused: this.state.paused,
      kill_switch: this.state.kill_switch,
    };
    const res = hardOnly(evaluateCore(p.order, this.mandate, snap, sv));
    let broker = null;
    if (res.decision === "ALLOW") {
      const fill = this.broker.execute(p.order);
      broker = { order_id: fill.id, status: "filled" };
      this.state.turnover_today = round2(
        this.state.turnover_today + res.order_value,
      );
      this.stats.executed += 1;
    } else {
      this.stats.denied += 1;
    }
    const entry = await this.journal.append(
      "confirm",
      {
        pending_id: pendingId,
        ref_seq: p.seq,
        order: p.order,
        mandate_hash: this.mandateHash,
        snapshot: snap,
        state_before: this._stateBefore(sv),
        checks: res.checks,
        decision: res.decision,
        explanation: res.explanation,
        order_value: res.order_value,
        actions: [],
        broker,
        agent: "client",
      },
      now,
    );
    return {
      ...res,
      kind: "confirm",
      pendingId,
      refSeq: p.seq,
      order: p.order,
      entry,
      broker,
    };
  }

  async reject(pendingId) {
    const p = this.pending.get(pendingId);
    if (!p) throw new Error("Заявка не ждёт подтверждения.");
    this.pending.delete(pendingId);
    this.stats.rejected += 1;
    const entry = await this.journal.append(
      "reject",
      {
        pending_id: pendingId,
        ref_seq: p.seq,
        order: p.order,
        mandate_hash: this.mandateHash,
        explanation: "Вы отклонили заявку: она не ушла брокеру.",
        agent: "client",
      },
      this.now,
    );
    return { kind: "reject", pendingId, refSeq: p.seq, order: p.order, entry };
  }

  async killSwitch(on) {
    this.state.kill_switch = !!on;
    const entry = await this.journal.append(
      "kill",
      { on: !!on, pending: [...this.pending.keys()] },
      this.now,
    );
    return { kind: "kill", on: !!on, entry };
  }

  async resume() {
    this.state.paused = false;
    this.state.violation_ts = [];
    const entry = await this.journal.append(
      "resume",
      { paused: false },
      this.now,
    );
    return { kind: "resume", entry };
  }
}
