"""Канонический JSON и хеш подписи (SPEC 1.2).

Ключи отсортированы, разделители без пробелов, не-ASCII без экранирования.
Числа печатаются как в ECMAScript (4121.0 -> 4121), чтобы хеши совпадали с JS.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


def _number(x: float) -> str:
    if not math.isfinite(x):
        raise ValueError("NaN и Infinity в каноническом JSON недопустимы")
    if x == 0:
        return "0"
    mantissa, _, exp = repr(abs(x)).partition("e")
    whole, _, frac = mantissa.partition(".")
    all_digits = whole + frac
    lead = len(all_digits) - len(all_digits.lstrip("0"))
    digits = all_digits.strip("0")
    n = len(whole) + int(exp or 0) - lead  # значение = 0.digits * 10**n
    k = len(digits)
    if k <= n <= 21:
        body = digits + "0" * (n - k)
    elif 0 < n <= 21:
        body = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        body = "0." + "0" * -n + digits
    else:
        e = n - 1
        tail = f"e{'+' if e >= 0 else '-'}{abs(e)}"
        body = digits + tail if k == 1 else f"{digits[0]}.{digits[1:]}{tail}"
    return "-" + body if x < 0 else body


def _write(value: Any, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        out.append(str(value))
    elif isinstance(value, float):
        out.append(_number(value))
    elif isinstance(value, str):
        out.append(json.dumps(value, ensure_ascii=False))
    elif isinstance(value, (list, tuple)):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _write(item, out)
        out.append("]")
    elif isinstance(value, dict):
        out.append("{")
        for i, key in enumerate(sorted(value, key=lambda k: k.encode("utf-16-be"))):
            if i:
                out.append(",")
            out.append(json.dumps(key, ensure_ascii=False))
            out.append(":")
            _write(value[key], out)
        out.append("}")
    else:
        raise TypeError(f"Тип {type(value).__name__} не сериализуется в канонический JSON")


def canonical_json(value: Any) -> str:
    out: list[str] = []
    _write(value, out)
    return "".join(out)


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
