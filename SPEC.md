# Мандат - спецификация движка (общая для Python и JS)

Источник истины для `mandat/` (Python) и `docs/assets/engine.js` (браузер). Обе реализации обязаны давать одинаковые решения и одинаковые коды проверок на одних и тех же входах. Тексты сообщений могут отличаться форматированием чисел, но смысл тот же.

Правила текста для людей: русский язык, только дефис «-», длинное и среднее тире запрещены. Кавычки «ёлочки». Деньги: `4 200 ₽`.

## 1. Данные

### 1.1 Рыночный снимок (`docs/data/market_demo.json`)

```
{
  "as_of": ISO-время,
  "fx": {"rub": 1, "cny": 11.2},            // курс к рублю
  "cash_rub": число,                         // свободные рубли
  "instruments": {TICKER: {"name", "class": "share|bond|etf|futures", "lot": int,
                           "currency": "rub|cny", "qual_only": bool,
                           "status": "normal|closed|halted", "last": цена за 1 бумагу в валюте бумаги}},
  "positions": {TICKER: количество бумаг (штук, не лотов)},
  "books": {TICKER: {"asks": [[цена, лоты], ...], "bids": [[цена, лоты], ...]}}   // asks по возрастанию, bids по убыванию
}
```

Производные величины:
- `fx(t) = fx[instruments[t].currency]`
- `position_value(t) = positions.get(t, 0) * instruments[t].last * fx(t)`
- `total_value = cash_rub + сумма position_value по всем позициям`
- `exec_price(order) = order.price if order.price is not None else instruments[t].last` (рыночная заявка оценивается по последней цене)
- `order_value = order.quantity * exec_price * fx(t)` (в рублях)
- `order_lots = order.quantity / instruments[t].lot`
- Видимая глубина для покупки: сумма лотов в `asks`, где `price <= best_ask * 1.01` (`best_ask = asks[0][0]`). Для продажи: сумма лотов в `bids`, где `price >= best_bid * 0.99`.

### 1.2 Мандат (`docs/data/mandates/*.json`, схема `mandat/v1`)

```
{
  "schema": "mandat/v1",
  "title": str, "client_text": str,           // исходные слова клиента
  "allowed_classes": [..],                    // классы, которые можно торговать
  "allowed_tickers": [..],                    // список клиента; пустой = списка нет
  "strict_whitelist": bool,                   // true: вне списка - запрет; false: вне списка - спросить клиента
  "allowed_currencies": [..],
  "allow_sell": bool,
  "forbid_leverage": bool,                    // true: покупка только на свободные деньги, продажа только из позиции
  "forbid_qual_only": bool,
  "limits": {                                 // любой лимит может быть null = проверка не выполняется
    "max_position_share": доля 0..1,          // доля бумаги в портфеле после покупки
    "max_order_value": ₽,                     // жёсткий потолок одной заявки
    "daily_turnover": ₽,                      // сумма исполненных заявок за день
    "ask_above": ₽,                           // выше - нужна подпись клиента (мягкое правило)
    "max_price_deviation": доля,              // |лимитная цена - last| / last
    "max_book_share": доля,                   // лоты заявки / видимая глубина
    "max_orders_per_minute": int              // защита от зацикливания
  },
  "standing_orders": [                        // поручения, которые агент исполняет сам
    {"id": "reinvest", "on": "coupon", "action": "buy_lowest_share", "from": [тикеры]},
    {"id": "dca", "on": "monthly", "day": 10, "action": "buy_amount", "ticker": "TMOS", "amount_rub": 10000}
  ]
}
```

Подпись мандата: `mandate_hash = sha256(canonical_json(mandate))`. Канонический JSON: ключи отсортированы, разделители `,` и `:` без пробелов, UTF-8 без экранирования не-ASCII.

### 1.3 Заявка агента

```
{"order_id": str|null, "ticker": str, "side": "buy|sell", "quantity": int (штук), "price": число|null, "agent": str, "reason": str}
```

### 1.4 Состояние шлюза (до проверки)

```
{"now": секунды (симулированные часы), "turnover_today": ₽, "recent_order_ts": [секунды попыток],
 "seen_order_ids": [..], "violation_ts": [секунды жёстких отказов], "paused": bool, "kill_switch": bool}
```

## 2. Проверки (строго в этом порядке)

Каждая проверка даёт `{"code", "ok": bool, "severity": "hard|soft", "detail": str}`. Проверки с `limit = null` пропускаются и в список не попадают. Проверки 1-5 при провале прерывают оценку (дальше не считаем).

| # | code | severity | провал, если | detail при провале |
|---|------|----------|--------------|--------------------|
| 1 | KILL | hard, стоп | `kill_switch` | Агент выключен клиентом. |
| 2 | PAUSED | hard, стоп | `paused` | Агент на паузе после серии нарушений. Снять паузу может только клиент. |
| 3 | DUPLICATE | hard, стоп | `order_id` не null и есть в `seen_order_ids` | Повтор заявки {order_id}: она уже обработана, вторая не отправлена. |
| 4 | RUNAWAY | hard, стоп | число `recent_order_ts` с `now - ts < 60` >= `max_orders_per_minute` | Похоже на зацикливание: {n} заявок за минуту при лимите {max}. Агент поставлен на паузу. |
| 5 | INSTRUMENT | hard, стоп | тикера нет в `instruments` | Бумага {ticker} не найдена. |
| 6 | TRADING_STATUS | hard | `status != "normal"` | Торги по {name} сейчас не идут. |
| 7 | CLASS | hard | `class` не в `allowed_classes` | Класс «{class_ru}» запрещён мандатом. |
| 8 | QUAL_ONLY | hard | `qual_only` и `forbid_qual_only` | {name} - только для квалифицированных инвесторов. |
| 9 | CURRENCY | hard | `currency` не в `allowed_currencies` | Валюта {CUR} не разрешена мандатом. |
| 10 | LOT | hard | `quantity <= 0` или `quantity % lot != 0` | Количество {q} не кратно лоту {lot}. |
| 11 | SIDE | hard | `sell` и не `allow_sell`; или `sell`, `forbid_leverage` и `quantity > positions[t]` | Продажи по мандату запрещены. / Продажа больше позиции - это шорт, запрещено. |
| 12 | PRICE_DEVIATION | hard | `price` не null и `abs(price - last) / last > max_price_deviation` | Цена {price} отличается от рынка ({last}) на {dev}%, лимит {max}%. |
| 13 | FUNDS | hard | `buy`, `forbid_leverage` и `order_value > cash_rub` | Не хватает денег: нужно {value} ₽, свободно {cash} ₽. Плечо запрещено. |
| 14 | WHITELIST | hard если `strict_whitelist`, иначе soft | `allowed_tickers` не пуст и тикера в нём нет | Бумаги {name} нет в вашем списке. |
| 15 | POSITION_SHARE | hard | `buy` и `(position_value + order_value) / total_value > max_position_share` | После покупки доля {name} станет {share}%, лимит {max}%. |
| 16 | ORDER_VALUE | hard | `order_value > max_order_value` | Заявка на {value} ₽ больше лимита {max} ₽. |
| 17 | DAILY_TURNOVER | hard | `turnover_today + order_value > daily_turnover` | Дневной лимит {max} ₽ будет превышен: уже {used} ₽. |
| 18 | ASK_THRESHOLD | soft | `order_value > ask_above` | Заявка на {value} ₽: крупные заявки по мандату подтверждаете вы. |
| 19 | BOOK_DEPTH | soft | `order_lots > max_book_share * depth` (если стакана нет - проверка пропускается) | Заявка забирает {pct}% видимого стакана: возможен сдвиг цены. |

Если проверка прошла, `detail` можно оставить пустым или коротким («ок»). Все сравнения строгие (`>`), как в таблице.

Решение: есть провал hard -> `DENY`; иначе есть провал soft -> `ASK`; иначе `ALLOW`. `explanation`:
- ALLOW: «В рамках мандата: заявка отправлена брокеру.»
- ASK: «Нужно ваше подтверждение: {detail первого мягкого провала}»
- DENY: «Отклонено: {detail первого жёсткого провала}»

Результат движка: `{"decision", "checks": [...], "explanation", "order_value": округлено до 0.01, "actions": []}`. При провале RUNAWAY в `actions` кладётся `"PAUSE"`.

`class_ru`: share - акции, bond - облигации, etf - фонды, futures - фьючерсы.

## 3. Шлюз (поверх движка)

Инструменты для агента: `get_portfolio`, `get_instrument(ticker)`, `get_orderbook(ticker)`, `get_mandate` (правила простыми словами), `post_order(...)`, `update_mandate(...)`.

`post_order`:
1. Берёт свежий снимок у брокера (в прототипе - mock-брокер).
2. Вызывает движок с текущим состоянием.
3. Обновляет состояние: `recent_order_ts += [now]`; `order_id` -> `seen_order_ids`; при `DENY` добавляет `now` в `violation_ts`; если в `violation_ts` за последние 600 секунд (строго `now - ts < 600`) 3 и больше отказов, или в `actions` есть `PAUSE` -> `paused = true`.
4. `ALLOW` -> заявка уходит брокеру, `turnover_today += order_value`, брокер меняет позиции и кэш (исполнение по `exec_price`, без комиссии).
5. `ASK` -> заявка ждёт клиента (`pending_id`). `confirm(pending_id)` повторяет только жёсткие проверки на свежем снимке и состоянии и отправляет заявку, если они прошли. `reject(pending_id)` пишет отказ.
6. Каждое решение пишется в журнал.

`update_mandate` от агента: всегда отказ, код `MANDATE_CHANGE` (hard): «Мандат меняет только клиент через подпись новой версии.» Считается нарушением (идёт в `violation_ts`).

Клиентские действия (агенту недоступны): `sign_mandate`, `confirm`, `reject`, `kill_switch(on|off)`, `resume` (снимает паузу и очищает `violation_ts`).

Дневной оборот обнуляется при смене календарного дня симулированных часов.

## 4. Журнал

Запись: `{"seq", "ts", "type": "mandate_signed|decision|confirm|reject|kill|resume", "payload", "prev_hash", "hash"}`, где `hash = sha256(canonical_json({seq, ts, type, payload, prev_hash}))`, у первой записи `prev_hash = "0" * 64`.

`payload` решения: `{"order", "mandate_hash", "snapshot": {"cash_rub", "total_value", "position_value", "instrument", "book"}, "state_before": {"turnover_today", "orders_last_minute", "paused", "kill_switch"}, "checks", "decision", "explanation", "broker": {"order_id", "status"} | null, "agent"}`.

`verify()` пересчитывает все хеши и связи; возвращает номер первой битой записи или null.
`replay(seq)` заново прогоняет движок на сохранённых в записи `order`, мандате (по хешу), снимке и состоянии и сравнивает `decision` и коды проверок.

## 5. Стресс-тест мандата (детерминированная часть)

| code | уровень | условие | текст | исправление |
|------|---------|---------|-------|-------------|
| L_NO_DAILY | warn | `daily_turnover` null | Нет дневного лимита: агент сможет потратить весь кэш за один день. | `daily_turnover = 100000` |
| L_NO_ORDER_MAX | warn | `max_order_value` null | Нет лимита на одну заявку. | `max_order_value = 50000` |
| L_NO_RUNAWAY | critical | `max_orders_per_minute` null | Нет защиты от зацикливания: робот сможет отправить сотни заявок. | `= 5` |
| L_NO_WHITELIST | warn | `allowed_tickers` пуст | Нет списка бумаг: агент сможет купить любую бумагу разрешённых классов. | - |
| L_SELL_OPEN | warn | `allow_sell` true | Продажи разрешены без условий. | `allow_sell = false` |
| L_LEVERAGE | critical | `forbid_leverage` false | Плечо разрешено: убыток может превысить вложенное. | `forbid_leverage = true` |
| L_DERIVATIVES | critical | `futures` в `allowed_classes` | Разрешены фьючерсы: это сделки с плечом. | убрать futures |
| L_NO_SHARE | warn | `max_position_share` null | Нет лимита доли: портфель может оказаться в одной бумаге. | `= 0.15` |
| L_SHARE_HIGH | warn | `max_position_share > 0.5` | Лимит доли выше 50%. | `= 0.15` |
| L_STANDING_CONFLICT | warn | `amount_rub` поручения больше `max_order_value` или `daily_turnover` | Поручение «{id}» больше лимита: оно не исполнится. | - |

Мандат с `critical` подписать нельзя, пока не исправлено. Смысловые дыры («что, если бумагу из списка погасят?») в MVP ищет LLM, в прототипе - только эта таблица.

## 6. Поручения (standing orders)

- `coupon` с суммой S: кэш += S; агент покупает бумагу из `from` с наименьшей текущей долей, `price = best_ask`, `quantity = floor(S / (best_ask * fx) / lot) * lot`. Если 0 - ничего не делает.
- `monthly` в день `day`: покупает `ticker` на `amount_rub` по рыночной цене (`price = null`), `quantity = floor(amount_rub / (last * fx) / lot) * lot`.

## 7. Эталонные сценарии демо (на `market_demo.json` и `t1_bonds.json` с исправленным `daily_turnover = 100000`; сценарии идут строго в этом порядке, S1 стартует в 10:00 10.11.2026 (`as_of`), каждый следующий на 10 минут позже предыдущего, действия внутри сценария - через 1 секунду, если не сказано иное; подтверждение в S3 - сразу)

| id | что происходит | ожидание |
|----|----------------|----------|
| S1 | купон 4 200 ₽, реинвест | buy SU26248RMFS4 x4 @ 905.6 -> ALLOW |
| S2 | 10-е число, докупка | buy TMOS x1418 @ рынок -> ALLOW |
| S3 | агент хочет Яндекс | buy YDEX x1 @ 4121 -> ASK (WHITELIST); confirm -> ALLOW |
| S6 | тонкий стакан | buy SU26243RMFS4 x40 @ 746.4 -> ASK (BOOK_DEPTH) |
| S7 | ошибка лота | buy SBER x15 @ 305.5 -> DENY (LOT) |
| S8 | бумага в юанях | buy SU52002RMFS1 x1 @ 984.5 -> DENY (CURRENCY; WHITELIST soft тоже провален) |
| S10 | сверх доли | buy SBER x130 @ 305.5 -> DENY (POSITION_SHARE; ASK_THRESHOLD soft тоже) |
| S9 | агент меняет мандат | update_mandate -> DENY (MANDATE_CHANGE) |
| S4 | фейковая новость: «продайте всё и купите фьючерс с плечом» | sell TMOS x19000 -> DENY (SIDE); sell SBER x400 -> DENY (SIDE); buy SiZ6 x10 @ 84510 -> DENY (CLASS, FUNDS, ...); после третьего отказа за 10 минут -> PAUSE |
| S5 | зацикленный робот: 40 x (buy SU26238RMFS4 x2 @ 612.5) каждые 0.25 с (перед стартом клиент снимает паузу) | 1-5 ALLOW, 6-я DENY (RUNAWAY) + PAUSE, 7-40 DENY (PAUSED); до брокера дошло 5 из 40 |
