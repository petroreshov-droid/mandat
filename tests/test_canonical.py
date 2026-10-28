import hashlib
import json

import pytest

from mandat.canonical import canonical_json, sha256_hex
from mandat.fixtures import DATA_DIR, load_mandate


def test_sorted_keys_and_no_spaces():
    assert canonical_json({"b": 1, "a": {"d": [1, 2], "c": None}}) == '{"a":{"c":null,"d":[1,2]},"b":1}'


def test_non_ascii_is_not_escaped():
    assert canonical_json({"имя": "Яндекс ₽"}) == '{"имя":"Яндекс ₽"}'


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (4121.0, "4121"),
        (905.6, "905.6"),
        (0.15, "0.15"),
        (-0.0, "0"),
        (1e-7, "1e-7"),
        (1.5e16, "15000000000000000"),
        (1e22, "1e+22"),
        (123456.789, "123456.789"),
        (-2.5, "-2.5"),
        (True, "true"),
        (False, "false"),
        (10**12, "1000000000000"),
    ],
)
def test_numbers_match_ecmascript(value, expected):
    assert canonical_json(value) == expected


def test_nan_and_unknown_types_are_rejected():
    with pytest.raises(ValueError):
        canonical_json(float("nan"))
    with pytest.raises(ValueError):
        canonical_json(float("inf"))
    with pytest.raises(TypeError):
        canonical_json({"x": {1, 2}})


def test_sha256_of_known_string():
    assert sha256_hex({"a": 1}) == hashlib.sha256(b'{"a":1}').hexdigest()


def test_key_order_does_not_change_hash():
    assert sha256_hex({"a": 1, "b": 2}) == sha256_hex({"b": 2, "a": 1})


def test_fixture_mandate_hash_is_over_canonical_utf8():
    raw = json.loads((DATA_DIR / "mandates" / "t1_bonds.json").read_text(encoding="utf-8"))
    expected = hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    assert sha256_hex(raw) == expected == sha256_hex(load_mandate("t1_bonds"))
