// Сборка статики демо. Запуск из docs/: node assets/build_data.mjs
// 1) assets/data.js - три JSON-фикстуры как ES-модуль (страница не делает fetch).
// 2) assets/demo.bundle.js - data.js + engine.js + scenarios.js + demo.js одним
//    классическим скриптом: Chrome не грузит ES-модули с file://, а демо должно
//    открываться и двойным кликом. По http(s) страница берёт настоящие модули.
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const readJSON = (rel) => JSON.parse(readFileSync(join(here, "..", "data", rel), "utf8"));

export const BUNDLE_SOURCES = ["data.js", "engine.js", "scenarios.js", "demo.js"];

export function sourcesHash(dir = here) {
  const h = createHash("sha256");
  for (const f of BUNDLE_SOURCES) h.update(readFileSync(join(dir, f), "utf8"));
  return h.digest("hex");
}

function buildData() {
  const market = readJSON("market_demo.json");
  const mandates = {
    t1: readJSON("mandates/t1_bonds.json"),
    t2: readJSON("mandates/t2_free.json"),
  };
  const out =
    "// Сгенерировано assets/build_data.mjs из docs/data/*.json. Не править вручную.\n" +
    `export const MARKET = ${JSON.stringify(market, null, 2)};\n\n` +
    `export const MANDATES = ${JSON.stringify(mandates, null, 2)};\n`;
  writeFileSync(join(here, "data.js"), out);
  return { market, mandates };
}

// Модуль -> фабрика в своей области видимости. Поддержаны только формы,
// которые реально есть в наших файлах: import { a, b as c } from "./x.js"
// и export const|let|function|function*|async function*|class.
function toFactory(file, src) {
  const imports = [];
  const names = [];
  let body = src.replace(/^import\s*\{([\s\S]*?)\}\s*from\s*["']\.\/([^"']+)["'];?[ \t]*$/gm, (_, list, from) => {
    const bindings = list
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
      .map((s) => {
        const m = /^([A-Za-z_$][\w$]*)(?:\s+as\s+([A-Za-z_$][\w$]*))?$/.exec(s);
        if (!m) throw new Error(`${file}: не разобрать импорт «${s}»`);
        return m[2] ? `${m[1]}: ${m[2]}` : m[1];
      });
    imports.push(`const { ${bindings.join(", ")} } = __mod[${JSON.stringify(from)}];`);
    return "";
  });
  body = body.replace(/^export\s+(async\s+function\s*\*?|function\s*\*?|class|const|let)\s*([A-Za-z_$][\w$]*)/gm, (_, kind, name) => {
    names.push(name);
    return `${kind} ${name}`;
  });
  if (/^\s*(import|export)\s/m.test(body)) throw new Error(`${file}: остались неподдерживаемые import/export`);
  return `// ---- ${file}\n__mod[${JSON.stringify(file)}] = (function () {\n${imports.join("\n")}\n${body}\nreturn { ${names.join(", ")} };\n})();\n`;
}

function buildBundle() {
  const parts = BUNDLE_SOURCES.map((f) => toFactory(f, readFileSync(join(here, f), "utf8")));
  const out =
    "// Сгенерировано assets/build_data.mjs: классическая сборка demo.js для file://. Не править вручную.\n" +
    `// source-hash: ${sourcesHash()}\n` +
    '(function () {\n"use strict";\nconst __mod = {};\n' +
    parts.join("\n") +
    "})();\n";
  writeFileSync(join(here, "demo.bundle.js"), out);
  return out.length;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const { market, mandates } = buildData();
  const size = buildBundle();
  console.log(`data.js: ${Object.keys(market.instruments).length} бумаг, мандаты ${Object.keys(mandates).join(", ")}`);
  console.log(`demo.bundle.js: ${Math.round(size / 1024)} КБ`);
}
