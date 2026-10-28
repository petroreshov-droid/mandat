import copy

import pytest
from conftest import NOW

from mandat import standing
from mandat.brokers import MockBroker
from mandat.clock import SimClock
from mandat.fixtures import demo_mandate, load_market
from mandat.gateway import Gateway


def codes(result):
    return [c["code"] for c in result["checks"] if not c["ok"]]


def test_coupon_goes_to_the_bond_with_the_lowest_share(gw, clock, broker):
    clock.advance(1)
    (done,) = standing.on_coupon(gw, 4200)
    assert done["standing_id"] == "reinvest"
    assert done["order"] == {"ticker": "SU26248RMFS4", "side": "buy", "quantity": 4, "price": 905.6}
    assert done["result"]["decision"] == "ALLOW"
    assert broker.snapshot()["cash_rub"] == pytest.approx(268653 + 4200 - 3622.4)
    assert broker.orders[0]["order"]["agent"] == "standing:reinvest"


def test_second_coupon_picks_the_bond_that_became_the_smallest(gw, clock):
    standing.on_coupon(gw, 4200)
    clock.advance(1)
    (done,) = standing.on_coupon(gw, 3800)  # 26248 выросла, теперь меньше всех 26243
    assert done["order"] == {"ticker": "SU26243RMFS4", "side": "buy", "quantity": 5, "price": 746.4}
    assert done["result"]["decision"] == "ALLOW"


def test_coupon_too_small_for_one_bond_places_nothing(gw, broker):
    (done,) = standing.on_coupon(gw, 500)
    assert done["order"] is None and done["result"] is None
    assert broker.orders == [] and broker.snapshot()["cash_rub"] == 269153.0  # деньги всё же пришли


def test_coupon_credit_can_be_left_to_the_broker(gw, broker):
    standing.on_coupon(gw, 4200, credit=False)
    assert broker.snapshot()["cash_rub"] == pytest.approx(268653 - 3622.4)


def test_coupon_event_id_makes_redelivery_harmless(gw, clock, broker):
    standing.on_coupon(gw, 4200, event_id="coupon-77")
    clock.advance(1)
    (again,) = standing.on_coupon(gw, 4200, event_id="coupon-77", credit=False)
    assert again["result"]["decision"] == "DENY" and codes(again["result"]) == ["DUPLICATE"]
    assert len(broker.orders) == 1


def test_ties_go_to_the_first_listed_ticker(t1, clock):
    market = load_market()
    for t in ("SU26238RMFS4", "SU26243RMFS4", "SU26248RMFS4"):
        market["positions"].pop(t)
    gw = Gateway(t1, MockBroker(market), clock=clock)
    (done,) = standing.on_coupon(gw, 4200)
    assert done["order"]["ticker"] == "SU26238RMFS4"


def test_coupon_without_a_book_places_nothing(t1, clock):
    market = load_market()
    market["books"]["SU26248RMFS4"]["asks"] = []
    gw = Gateway(t1, MockBroker(market), clock=clock)
    (done,) = standing.on_coupon(gw, 4200)
    assert done["order"] is None


def test_monthly_runs_only_on_its_day(gw, clock, broker):
    clock.advance(1)  # 10 ноября
    (done,) = standing.on_monthly(gw)
    assert done["order"] == {"ticker": "TMOS", "side": "buy", "quantity": 1418, "price": None}
    assert done["result"]["decision"] == "ALLOW" and done["result"]["order_value"] == 9996.9
    assert standing.on_monthly(gw, day=9) == []
    assert len(broker.orders) == 1


def test_monthly_uses_the_gateway_clock(t1, broker):
    clock = SimClock(NOW + 24 * 3600)  # 11 ноября
    gw = Gateway(t1, broker, clock=clock)
    assert standing.on_monthly(gw) == []
    (done,) = standing.on_monthly(gw, day=10)
    assert done["result"]["decision"] == "ALLOW"


def test_monthly_runs_once_per_month(gw, clock):
    standing.on_monthly(gw)
    clock.advance(1)
    (again,) = standing.on_monthly(gw)
    assert codes(again["result"]) == ["DUPLICATE"]


def test_standing_orders_pass_through_the_same_checks(clock, broker):
    mandate = copy.deepcopy(demo_mandate())
    mandate["standing_orders"][1]["amount_rub"] = 60000  # больше лимита заявки: lint предупредит, шлюз подпишет
    gw = Gateway(mandate, broker, clock=clock)
    (done,) = standing.on_monthly(gw)
    assert done["result"]["decision"] == "DENY" and "ORDER_VALUE" in codes(done["result"])
    assert broker.orders == []


def test_coupon_credit_needs_a_broker_that_can_credit(t1, clock):
    class SelfCreditingBroker(MockBroker):
        credit_cash = None

    gw = Gateway(t1, SelfCreditingBroker(), clock=clock)
    with pytest.raises(TypeError, match="credit=False"):
        standing.on_coupon(gw, 4200)
    assert standing.on_coupon(gw, 4200, credit=False)[0]["order"] is not None
