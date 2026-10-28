import copy

import pytest
from conftest import assert_no_long_dashes

from mandat.fixtures import demo_mandate, load_mandate
from mandat.lint import apply_fixes, has_critical, lint


def codes(findings):
    return [f["code"] for f in findings]


def mutate(mandate, **changes):
    m = copy.deepcopy(mandate)
    for path, value in changes.items():
        target = m
        *parents, leaf = path.split("__")
        for key in parents:
            target = target[key]
        target[leaf] = value
    return m


def test_t1_has_exactly_one_warning_no_daily_limit():
    findings = lint(load_mandate("t1_bonds"))
    assert codes(findings) == ["L_NO_DAILY"]
    assert findings[0]["level"] == "warn"
    assert findings[0]["text"] == "Нет дневного лимита: агент сможет потратить весь кэш за один день."
    assert findings[0]["fix"]["text"] == "daily_turnover = 100000"
    assert not has_critical(findings)


def test_demo_mandate_is_clean():
    assert lint(demo_mandate()) == []


def test_t2_free_mandate_has_the_critical_holes():
    findings = lint(load_mandate("t2_free"))
    assert codes(findings) == [
        "L_NO_DAILY", "L_NO_ORDER_MAX", "L_NO_RUNAWAY", "L_NO_WHITELIST",
        "L_SELL_OPEN", "L_LEVERAGE", "L_DERIVATIVES", "L_NO_SHARE",
    ]  # fmt: skip
    critical = {f["code"] for f in findings if f["level"] == "critical"}
    assert critical == {"L_NO_RUNAWAY", "L_LEVERAGE", "L_DERIVATIVES"}
    assert has_critical(findings)


@pytest.mark.parametrize(
    ("code", "level", "changes"),
    [
        ("L_NO_DAILY", "warn", {"limits__daily_turnover": None}),
        ("L_NO_ORDER_MAX", "warn", {"limits__max_order_value": None}),
        ("L_NO_RUNAWAY", "critical", {"limits__max_orders_per_minute": None}),
        ("L_NO_WHITELIST", "warn", {"allowed_tickers": []}),
        ("L_SELL_OPEN", "warn", {"allow_sell": True}),
        ("L_LEVERAGE", "critical", {"forbid_leverage": False}),
        ("L_DERIVATIVES", "critical", {"allowed_classes": ["bond", "futures"]}),
        ("L_NO_SHARE", "warn", {"limits__max_position_share": None}),
        ("L_SHARE_HIGH", "warn", {"limits__max_position_share": 0.51}),
    ],
)
def test_each_hole_is_found_alone(code, level, changes):
    findings = lint(mutate(demo_mandate(), **changes))
    assert codes(findings) == [code]
    assert findings[0]["level"] == level


def test_share_limit_of_exactly_half_is_not_flagged():
    assert lint(mutate(demo_mandate(), limits__max_position_share=0.5)) == []


def test_standing_order_larger_than_order_limit_or_daily_limit():
    base = demo_mandate()
    over_order = mutate(base, limits__max_order_value=5000)  # DCA 10000 > 5000
    found = lint(over_order)
    assert codes(found) == ["L_STANDING_CONFLICT"]
    assert found[0]["text"] == "Поручение «dca» больше лимита: оно не исполнится."
    over_daily = mutate(base, limits__daily_turnover=9999)
    assert codes(lint(over_daily)) == ["L_STANDING_CONFLICT"]
    equal = mutate(base, limits__daily_turnover=10000)
    assert lint(equal) == []  # строго больше


def test_apply_fixes_clears_everything_that_has_a_fix_and_keeps_the_original():
    t2 = load_mandate("t2_free")
    original = copy.deepcopy(t2)
    fixed = apply_fixes(t2, lint(t2))
    assert t2 == original
    assert codes(lint(fixed)) == ["L_NO_WHITELIST"]  # у этого замечания исправления нет
    assert "futures" not in fixed["allowed_classes"]
    assert fixed["limits"]["max_orders_per_minute"] == 5 and fixed["forbid_leverage"] is True


def test_apply_fixes_only_selected_codes():
    t2 = load_mandate("t2_free")
    fixed = apply_fixes(t2, lint(t2), codes={"L_LEVERAGE"})
    assert fixed["forbid_leverage"] is True and fixed["allow_sell"] is True


def test_texts_use_only_hyphen():
    for mandate in (load_mandate("t1_bonds"), load_mandate("t2_free")):
        for f in lint(mandate):
            text = f["text"] + (f["fix"]["text"] if f["fix"] else "")
            assert_no_long_dashes(text)
