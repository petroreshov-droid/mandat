import copy

import pytest

from mandat.brokers import MockBroker
from mandat.clock import SimClock
from mandat.fixtures import demo_mandate, demo_start, load_mandate, load_market
from mandat.gateway import Gateway
from mandat.models import fresh_state
from mandat.rules import evaluate

NOW = demo_start()  # 10:00 10.11.2026 МСК
LONG_DASHES = (chr(0x2014), chr(0x2013))  # в текстах для людей разрешён только дефис


def assert_no_long_dashes(text: str) -> None:
    assert not any(d in text for d in LONG_DASHES), "в тексте длинное или среднее тире"


@pytest.fixture
def market():
    return load_market()


@pytest.fixture
def t1():
    return demo_mandate()


@pytest.fixture
def t2():
    return load_mandate("t2_free")


def make_order(**kw):
    order = {
        "order_id": None,
        "ticker": "TMOS",
        "side": "buy",
        "quantity": 1,
        "price": None,
        "agent": "test",
        "reason": "",
    }
    order.update(kw)
    return order


def code_result(result, code):
    """Проверка по коду из результата движка или None, если её нет в списке."""
    return next((c for c in result["checks"] if c["code"] == code), None)


def failed(result):
    return [c["code"] for c in result["checks"] if not c["ok"]]


@pytest.fixture
def ev(market, t1):
    """ev(order, mandate=..., market=..., state=...): evaluate с t1, демо-рынком и чистым состоянием.

    Переопределения вкладываются: limits={...} дописывает лимиты, state={...} - состояние.
    """

    def run(order=None, *, limits=None, mandate=None, market_patch=None, state=None, **order_kw):
        m = copy.deepcopy(mandate if mandate is not None else t1)
        m["limits"].update(limits or {})
        mk = copy.deepcopy(market)
        for key, patch in (market_patch or {}).items():
            if isinstance(patch, dict) and isinstance(mk.get(key), dict):
                mk[key].update(patch)
            else:
                mk[key] = patch
        st = fresh_state(now=NOW, **(state or {}))
        return evaluate(order or make_order(**order_kw), m, mk, st)

    return run


@pytest.fixture
def clock():
    return SimClock(NOW)


@pytest.fixture
def broker():
    return MockBroker()


@pytest.fixture
def gw(t1, broker, clock):
    return Gateway(t1, broker, clock=clock)
