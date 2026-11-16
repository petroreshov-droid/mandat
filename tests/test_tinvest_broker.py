"""Адаптер T-Invest MCP на фейковом сервере. Ответы повторяют форматы песочницы (сверено 16.11.2026)."""

import json

import pytest

from mandat.brokers.base import BrokerError
from mandat.brokers.tinvest_mcp import TInvestMcpBroker, TInvestMcpClient
from mandat.clock import SimClock
from mandat.fixtures import demo_mandate, demo_start
from mandat.gateway import Gateway

TOKEN = "t.SECRET-TOKEN-123"
ACC = "acc-1"
UNIVERSE = {"SBER": "SBER_TQBR", "TMOS": "TMOS_TQTF", "SU26238RMFS4": "SU26238RMFS4_TQOB"}

CARDS = {
    "SBER_TQBR": {
        "ticker": "SBER",
        "classCode": "TQBR",
        "lot": 1,
        "currency": "rub",
        "name": "Сбербанк",
        "apiTradeAvailableFlag": True,
        "forQualInvestorFlag": False,
    },
    "LKOH_TQBR": {
        "ticker": "LKOH",
        "classCode": "TQBR",
        "lot": 1,
        "currency": "rub",
        "name": "Лукойл",
        "apiTradeAvailableFlag": True,
        "forQualInvestorFlag": False,
    },
    "GAZP_TQBR": {
        "ticker": "GAZP",
        "classCode": "TQBR",
        "lot": 10,
        "currency": "rub",
        "name": "Газпром",
        "apiTradeAvailableFlag": True,
        "forQualInvestorFlag": False,
    },
    "TMOS_TQTF": {
        "ticker": "TMOS",
        "classCode": "TQTF",
        "lot": 1,
        "currency": "rub",
        "name": "Крупнейшие компании РФ",
        "apiTradeAvailableFlag": False,
        "forQualInvestorFlag": False,
    },
    "SU26238RMFS4_TQOB": {
        "ticker": "SU26238RMFS4",
        "classCode": "TQOB",
        "lot": 1,
        "currency": "rub",
        "name": "ОФЗ 26238",
        "nominal": {"currency": "rub", "value": "1000"},
        "apiTradeAvailableFlag": True,
        "forQualInvestorFlag": False,
    },
}
LAST = {
    "SBER_TQBR": "295.95",
    "LKOH_TQBR": "5974.5",
    "GAZP_TQBR": "120.5",
    "TMOS_TQTF": "5.78",
    "SU26238RMFS4_TQOB": "51.401",
}


def mv(iid, value):
    ticker, board = iid.rsplit("_", 1)
    return {
        "ticker": ticker,
        "classCode": board,
        "values": [
            {"type": "INSTRUMENT_VALUE_CLOSE_PRICE", "value": {"value": "1"}},
            {"type": "INSTRUMENT_VALUE_LAST_PRICE", "value": {"value": value}},
        ],
    }


class FakeTInvest:
    """MCP-сервер песочницы в миниатюре: отвечает на tools/call по имени инструмента."""

    def __init__(self):
        self.calls = []
        self.cash = "1000000"
        self.securities = [{"ticker": "SBER", "classCode": "TQBR", "balance": "3", "blocked": "0"}]
        self.order_reply = {
            "orderId": "ord-1",
            "executionReportStatus": "EXECUTION_REPORT_STATUS_FILL",
            "lotsExecuted": "1",
            "executedOrderPrice": {"currency": "rub", "value": "295.9"},
        }
        self.order_error = None
        self.down = False

    def __call__(self, url, headers, body):
        msg = json.loads(body)
        method = msg.get("method")
        if method == "initialize":
            return 200, {"content-type": "application/json"}, self._ok(msg, {"protocolVersion": "2025-06-18"})
        if method == "notifications/initialized":
            return 202, {}, b""
        name, args = msg["params"]["name"], msg["params"]["arguments"]
        self.calls.append((name, args))
        if self.down:
            return 503, {}, b"unavailable"
        if name in ("invest_get_share", "invest_get_etf", "invest_get_bond") and args["instrumentId"] not in CARDS:
            text = '{"type":"NOT_FOUND","message":"Instrument not found"}'
            return (
                200,
                {"content-type": "application/json"},
                self._ok(msg, {"isError": True, "content": [{"type": "text", "text": text}]}),
            )
        if name == "invest_create_order" and self.order_error:
            return (
                200,
                {"content-type": "application/json"},
                self._ok(msg, {"isError": True, "content": [{"type": "text", "text": self.order_error}]}),
            )
        return (
            200,
            {"content-type": "application/json"},
            self._ok(msg, {"content": [{"type": "text", "text": json.dumps(self.result(name, args))}]}),
        )

    def result(self, name, args):
        if name == "invest_get_positions":
            return {"money": [{"currency": "rub", "value": self.cash}], "blocked": [], "securities": self.securities}
        if name in ("invest_get_share", "invest_get_etf", "invest_get_bond"):
            return {"instrument": CARDS[args["instrumentId"]]}
        if name == "invest_get_trading_statuses":
            out = []
            for iid in args["instrumentId"]:
                ticker, board = iid.rsplit("_", 1)
                closed = iid == "TMOS_TQTF"
                out.append(
                    {
                        "ticker": ticker,
                        "classCode": board,
                        "tradingStatus": "SECURITY_TRADING_STATUS_NOT_AVAILABLE_FOR_TRADING"
                        if closed
                        else "SECURITY_TRADING_STATUS_TRADING_AT_CLOSING_AUCTION_PRICE",
                        "limitOrderAvailableFlag": not closed,
                        "marketOrderAvailableFlag": False,
                    }
                )
            return {"tradingStatuses": out}
        if name == "invest_get_market_values":
            rates = {"CNYRUB_TOM_CETS": "12.402"}
            return {"instruments": [mv(i, LAST.get(i) or rates[i]) for i in args["instrumentId"]]}
        if name == "invest_get_orderbook":
            if args["instrumentId"] == "SU26238RMFS4_TQOB":
                return {
                    "priceCurrency": "pt.",
                    "asks": [{"price": {"value": "51.442"}, "quantity": "59"}],
                    "bids": [{"price": {"value": "51.401"}, "quantity": "1352"}],
                }
            return {
                "priceCurrency": "rub",
                "asks": [
                    {"price": {"value": "296"}, "quantity": "369"},
                    {"price": {"value": "296.2"}, "quantity": "1014"},
                ],
                "bids": [{"price": {"value": "295.9"}, "quantity": "108"}],
            }
        if name == "invest_find_instrument":
            return {
                "instruments": [
                    {"ticker": args["query"], "classCode": "041", "apiTradeAvailableFlag": False},
                    {"ticker": args["query"] + "-RM", "classCode": "SPBXM", "apiTradeAvailableFlag": True},
                    {"ticker": args["query"], "classCode": "TQBR", "apiTradeAvailableFlag": True},
                ]
            }
        if name == "invest_create_order":
            return self.order_reply
        raise AssertionError(name)

    @staticmethod
    def _ok(msg, result):
        return json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}).encode()


def broker(server=None, **kw):
    server = server or FakeTInvest()
    client = TInvestMcpClient(token=TOKEN, transport=server)
    return TInvestMcpBroker(client, account_id=ACC, universe=dict(UNIVERSE), **kw), server


def names(server):
    return [n for n, _ in server.calls]


def test_account_is_required(monkeypatch):
    monkeypatch.delenv("TINVEST_ACCOUNT_ID", raising=False)
    with pytest.raises(BrokerError, match="TINVEST_ACCOUNT_ID"):
        TInvestMcpBroker(TInvestMcpClient(token=TOKEN, transport=FakeTInvest()))


def test_snapshot_has_the_spec_shape_and_converts_units():
    b, server = broker()
    m = b.snapshot()
    assert set(m) == {"as_of", "fx", "cash_rub", "instruments", "positions", "books"}
    assert m["cash_rub"] == 1_000_000 and m["positions"] == {"SBER": 3} and m["fx"] == {"rub": 1.0}
    sber, ofz = m["instruments"]["SBER"], m["instruments"]["SU26238RMFS4"]
    assert sber["class"] == "share" and sber["lot"] == 1 and sber["last"] == 295.95 and sber["status"] == "normal"
    assert ofz["class"] == "bond" and ofz["last"] == pytest.approx(514.01)  # 51.401% от номинала 1000
    assert m["instruments"]["TMOS"]["status"] == "halted"  # через API не торгуется
    # статусы и цены - одним вызовом на все бумаги
    assert names(server).count("invest_get_trading_statuses") == 1
    assert names(server).count("invest_get_market_values") == 1


def test_books_are_fetched_only_for_the_ticker_asked_and_in_rubles():
    b, server = broker()
    m = b.snapshot()
    assert "invest_get_orderbook" not in names(server)
    assert m["books"].get("SU26238RMFS4")["asks"] == [[pytest.approx(514.42), 59.0]]
    assert m["books"]["SBER"]["asks"][0] == [296.0, 369.0]
    assert m["books"].get("NOPE") is None and m["books"].get("NOPE", {}) == {}
    assert names(server).count("invest_get_orderbook") == 2
    m["books"].get("SBER")
    assert names(server).count("invest_get_orderbook") == 2  # стакан в рамках снимка не перечитывается


def test_cards_are_cached_between_snapshots():
    b, server = broker()
    b.snapshot()
    b.snapshot()
    assert names(server).count("invest_get_share") == 1
    assert names(server).count("invest_get_positions") == 2


def test_unknown_ticker_resolves_to_main_board_with_api_access():
    b, server = broker()
    b.place_order({"ticker": "GAZP", "side": "buy", "quantity": 10, "price": 120.5})
    find = [a for n, a in server.calls if n == "invest_find_instrument"]
    assert find and find[0]["query"] == "GAZP"
    order = next(a for n, a in server.calls if n == "invest_create_order")
    assert order["instrumentId"] == "GAZP_TQBR" and order["quantity"] == 1  # 10 штук = 1 лот


def test_share_order_is_sent_in_lots_with_currency_price_and_no_margin():
    b, server = broker()
    res = b.place_order({"ticker": "SBER", "side": "buy", "quantity": 1, "price": 296.0})
    args = next(a for n, a in server.calls if n == "invest_create_order")
    assert args == {
        "accountId": ACC,
        "instrumentId": "SBER_TQBR",
        "direction": "ORDER_DIRECTION_BUY",
        "quantity": 1,
        "confirmMarginTrade": False,
        "orderType": "ORDER_TYPE_LIMIT",
        "price": {"value": "296"},
        "priceType": "PRICE_TYPE_CURRENCY",
    }
    assert res["status"] == "filled" and res["order_id"] == "ord-1" and res["exec_price"] == 295.9


def test_bond_price_goes_in_points_and_market_order_has_no_price():
    b, server = broker()
    b.place_order({"ticker": "SU26238RMFS4", "side": "buy", "quantity": 1, "price": 514.5})
    b.place_order({"ticker": "SBER", "side": "sell", "quantity": 2, "price": None})
    bond, market = [a for n, a in server.calls if n == "invest_create_order"]
    assert bond["price"] == {"value": "51.45"} and bond["priceType"] == "PRICE_TYPE_POINT"
    assert market["orderType"] == "ORDER_TYPE_MARKET" and "price" not in market
    assert market["direction"] == "ORDER_DIRECTION_SELL"


def test_quantity_not_multiple_of_lot_is_rejected_without_calling_the_broker():
    b, server = broker()
    res = b.place_order({"ticker": "GAZP", "side": "buy", "quantity": 15, "price": 120.5})
    assert res["status"] == "rejected" and "лоту" in res["reason"]
    assert "invest_create_order" not in names(server)


def test_broker_refusal_is_rejected_and_outage_raises():
    server = FakeTInvest()
    server.order_error = '{"type":"INVALID_ARGUMENT","message":"The price is outside the limits for this instrument"}'
    b, _ = broker(server)
    res = b.place_order({"ticker": "SBER", "side": "buy", "quantity": 1, "price": 1.0})
    assert res["status"] == "rejected" and "outside the limits" in res["reason"]
    server.order_error = '{"type":"INTERNAL","message":"Unexpected error"}'
    with pytest.raises(BrokerError):  # исход неизвестен: шлюз запишет error
        b.place_order({"ticker": "SBER", "side": "buy", "quantity": 1, "price": 296.0})


def test_foreign_currency_needs_a_rate():
    server = FakeTInvest()
    CARDS["LKOH_TQBR"] = {**CARDS["LKOH_TQBR"], "currency": "cny"}
    try:
        b, _ = broker(
            server,
        )
        b.universe["LKOH"] = "LKOH_TQBR"
        assert b.snapshot()["fx"]["cny"] == 12.402
    finally:
        CARDS["LKOH_TQBR"] = {**CARDS["LKOH_TQBR"], "currency": "rub"}


# --- шлюз Мандата поверх адаптера ---


def gateway(server=None):
    b, server = broker(server)
    return Gateway(demo_mandate(), b, clock=SimClock(demo_start())), server


def test_gateway_allows_inside_the_mandate_and_sends_to_the_bank():
    gw, server = gateway()
    res = gw.post_order("SBER", "buy", 1, 296.0, order_id="a1", agent="tlite")
    assert res["decision"] == "ALLOW" and res["broker"]["status"] == "filled"
    assert names(server).count("invest_create_order") == 1


def test_gateway_stops_what_the_bank_would_execute():
    gw, server = gateway()
    sell = gw.post_order("SBER", "sell", 1, 295.9, order_id="s1")
    dup1 = gw.post_order("SBER", "buy", 1, 296.0, order_id="d1")
    dup2 = gw.post_order("SBER", "buy", 1, 296.0, order_id="d1")  # повтор: у банка он стал бы второй сделкой
    tmos = gw.post_order("TMOS", "buy", 1, 5.78, order_id="t1")
    assert sell["decision"] == "DENY" and dup1["decision"] == "ALLOW" and dup2["decision"] == "DENY"
    assert any(c["code"] == "DUPLICATE" and not c["ok"] for c in dup2["checks"])
    assert tmos["decision"] == "DENY" and any(c["code"] == "TRADING_STATUS" and not c["ok"] for c in tmos["checks"])
    assert names(server).count("invest_create_order") == 1


def test_gateway_fails_closed_and_journals_when_the_bank_is_down():
    server = FakeTInvest()
    server.down = True
    gw, _ = gateway(server)
    res = gw.post_order("SBER", "buy", 1, 296.0, order_id="x1")
    assert res["decision"] == "DENY" and res["checks"][0]["code"] == "BROKER_UNAVAILABLE"
    assert TOKEN not in json.dumps(res, ensure_ascii=False)
    assert gw.state.violation_ts == [] and not res["paused"]  # не вина агента
    rec = gw.journal.records[-1]
    assert rec["payload"]["kind"] == "broker_unavailable" and rec["payload"]["order"]["ticker"] == "SBER"
    assert "invest_create_order" not in names(server)


def test_listed_ticker_unknown_to_the_bank_is_skipped_but_a_held_one_stops_the_check():
    b, server = broker()
    b.universe["SU26248RMFS4"] = "SU26248RMFS4_TQOB"  # в демо-мандате был неверный тикер (верный SU26248RMFS3)
    m = b.snapshot()
    assert "SU26248RMFS4" not in m["instruments"] and "SU26248RMFS4" in b.unavailable
    server.securities.append({"ticker": "XXXX", "classCode": "TQBR", "balance": "1"})
    with pytest.raises(BrokerError):
        b.snapshot()
