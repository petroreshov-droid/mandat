import copy
import io
import json
import urllib.error

import pytest
from conftest import assert_no_long_dashes

from mandat.brokers import MockBroker
from mandat.compiler import (
    MANDATE_SCHEMA,
    REQUIRED_QUESTIONS,
    SYSTEM_PROMPT,
    TOP_KEYS,
    FixtureProvider,
    OpenAICompatibleProvider,
    ProviderError,
    compile_mandate,
    parse_reply,
    validate,
)
from mandat.fixtures import all_mandates, load_mandate, load_market
from mandat.gateway import Gateway, MandateRejected

T1_TEXT = load_mandate("t1_bonds")["client_text"]
T2_TEXT = load_mandate("t2_free")["client_text"]


class Canned:
    """Провайдер с заранее заданным ответом."""

    def __init__(self, reply):
        self.reply = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        self.calls = []

    def complete(self, system_prompt, user_message):
        self.calls.append((system_prompt, user_message))
        return self.reply


DELETE = object()


def edit(mandate, **changes):
    """Копия мандата с правками по путям вида limits__daily_turnover или standing_orders__1__day."""
    m = copy.deepcopy(mandate)
    for path, value in changes.items():
        *parents, leaf = path.split("__")
        target = m
        for key in parents:
            target = target[int(key)] if key.isdigit() else target[key]
        leaf_key = int(leaf) if leaf.isdigit() else leaf
        if value is DELETE:
            del target[leaf_key]
        else:
            target[leaf_key] = value
    return m


# --- validate ---


@pytest.mark.parametrize("name", ["t1_bonds", "t2_free"])
def test_fixture_mandates_are_valid(name):
    assert validate(load_mandate(name)) == []


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"allow_sell": DELETE}, "Нет значения для «allow_sell»"),
        ({"limits__daily_turnover": DELETE}, "Нет значения для «limits.daily_turnover»"),
        ({"extra": 1}, "«extra»: лишнее поле"),
        ({"limits__extra": 1}, "«limits.extra»: лишнее поле"),
        ({"schema": "mandat/v2"}, "«schema»"),
        ({"allowed_classes": ["crypto"]}, "«allowed_classes»"),
        ({"allowed_classes": []}, "«allowed_classes»"),
        ({"allowed_classes": ["bond", "bond"]}, "«allowed_classes»"),
        ({"allowed_tickers": ["A", "A"]}, "«allowed_tickers»"),
        ({"allowed_tickers": "SBER"}, "«allowed_tickers»"),
        ({"strict_whitelist": "yes"}, "«strict_whitelist»"),
        ({"allowed_currencies": ["RUB"]}, "«allowed_currencies»"),
        ({"allowed_currencies": []}, "«allowed_currencies»"),
        ({"allow_sell": 1}, "«allow_sell»"),
        ({"title": ""}, "«title»"),
        ({"limits__max_position_share": 1.5}, "«limits.max_position_share»"),
        ({"limits__max_position_share": 0}, "«limits.max_position_share»"),
        ({"limits__max_order_value": -1}, "«limits.max_order_value»"),
        ({"limits__ask_above": "30000"}, "«limits.ask_above»"),
        ({"limits__max_orders_per_minute": 0}, "«limits.max_orders_per_minute»"),
        ({"limits__max_orders_per_minute": 2.5}, "«limits.max_orders_per_minute»"),
        ({"limits__max_orders_per_minute": True}, "«limits.max_orders_per_minute»"),
        ({"limits": []}, "«limits»"),
        ({"standing_orders": {}}, "«standing_orders»"),
        ({"standing_orders__0__on": "weekly"}, "standing_orders[0]"),
        ({"standing_orders__0__from": []}, "standing_orders[0]"),
        ({"standing_orders__0__extra": 1}, "лишнее поле «extra»"),
        ({"standing_orders__1__day": 32}, "standing_orders[1]"),
        ({"standing_orders__1__day": True}, "standing_orders[1]"),
        ({"standing_orders__1__amount_rub": 0}, "standing_orders[1]"),
        ({"standing_orders__1__id": "reinvest"}, "повторяется id"),
    ],
)
def test_validate_reports_problems(changes, fragment):
    problems = validate(edit(load_mandate("t1_bonds"), **changes))
    assert any(fragment in p for p in problems), problems


def test_validate_non_object():
    assert validate([]) == ["Мандат должен быть JSON-объектом."]
    assert validate(None) == ["Мандат должен быть JSON-объектом."]


def test_schema_dict_agrees_with_fixtures_and_validate():
    jsonschema = pytest.importorskip("jsonschema")
    assert set(MANDATE_SCHEMA["required"]) == set(TOP_KEYS)
    for mandate in all_mandates().values():
        jsonschema.validate(mandate, MANDATE_SCHEMA)
    for broken in (
        edit(load_mandate("t1_bonds"), allow_sell=DELETE),
        edit(load_mandate("t1_bonds"), allowed_classes=["crypto"]),
        edit(load_mandate("t1_bonds"), limits__max_position_share=1.5),
        edit(load_mandate("t1_bonds"), limits__max_orders_per_minute=0),
        edit(load_mandate("t1_bonds"), extra=1),
    ):
        assert validate(broken)
        assert not jsonschema.Draft202012Validator(MANDATE_SCHEMA).is_valid(broken)


# --- FixtureProvider ---


def test_fixture_provider_maps_both_client_texts_to_their_json():
    for name, text in (("t1_bonds", T1_TEXT), ("t2_free", T2_TEXT)):
        result = compile_mandate(text, FixtureProvider())
        assert result.ok and result.mandate == load_mandate(name) and result.questions == []


def test_fixture_provider_ignores_whitespace_differences():
    messy = "  " + T1_TEXT.replace(". ", ".\n   ") + "\n"
    assert compile_mandate(messy, FixtureProvider()).ok


def test_compile_returns_lint_next_to_the_draft():
    codes = [f["code"] for f in compile_mandate(T1_TEXT, FixtureProvider()).lint]
    assert codes == ["L_NO_DAILY"]
    critical = {f["code"] for f in compile_mandate(T2_TEXT, FixtureProvider()).lint if f["level"] == "critical"}
    assert critical == {"L_NO_RUNAWAY", "L_LEVERAGE", "L_DERIVATIVES"}


def test_unknown_text_gets_questions_not_a_guess():
    result = compile_mandate("Купи что-нибудь хорошее", FixtureProvider())
    assert not result.ok and result.mandate is None
    assert len(result.questions) >= 3
    assert (
        REQUIRED_QUESTIONS["allow_sell"] in result.questions
        and REQUIRED_QUESTIONS["forbid_leverage"] in result.questions
    )


def test_provider_receives_prompt_text_and_instrument_directory():
    provider = Canned({"questions": ["Что именно?"]})
    result = compile_mandate("Хочу ОФЗ", provider, directory=load_market()["instruments"])
    system, user = provider.calls[0]
    assert system == SYSTEM_PROMPT and "Хочу ОФЗ" in user
    assert "SU26238RMFS4 - ОФЗ 26238 (bond, rub)" in user
    assert result.questions == ["Что именно?"]


# --- вопросы вместо догадок ---


def test_missing_required_fields_become_questions():
    partial = {k: v for k, v in load_mandate("t1_bonds").items() if k not in ("allow_sell", "forbid_leverage")}
    result = compile_mandate("Покупай ОФЗ", Canned({"mandate": partial}))
    assert result.mandate is None and result.problems == []
    assert result.questions == [REQUIRED_QUESTIONS["allow_sell"], REQUIRED_QUESTIONS["forbid_leverage"]]


def test_null_in_a_required_flag_is_a_question_too():
    draft = edit(load_mandate("t1_bonds"), allow_sell=None)
    result = compile_mandate("x", Canned(draft))  # ответ без обёртки «mandate» тоже принимается
    assert result.questions == [REQUIRED_QUESTIONS["allow_sell"]]


def test_missing_limit_key_is_a_question_but_explicit_null_is_a_decision():
    draft = edit(load_mandate("t1_bonds"), limits__ask_above=DELETE)
    assert compile_mandate("x", Canned(draft)).questions == [REQUIRED_QUESTIONS["limits.ask_above"]]
    draft = edit(load_mandate("t1_bonds"), limits__ask_above=None)
    assert compile_mandate("x", Canned(draft)).ok


def test_wrong_values_are_problems_not_questions():
    draft = edit(load_mandate("t1_bonds"), limits__max_position_share=15)  # забыли перевести % в долю
    result = compile_mandate("x", Canned(draft))
    assert result.mandate is None and result.questions == []
    assert "limits.max_position_share" in result.problems[0]


def test_client_words_and_schema_are_forced_not_trusted():
    draft = edit(load_mandate("t1_bonds"), client_text="перефразировано моделью", schema="other")
    result = compile_mandate("  Слова клиента дословно  ", Canned(draft))
    assert result.ok and result.mandate["client_text"] == "Слова клиента дословно"
    assert result.mandate["schema"] == "mandat/v1"


def test_missing_title_gets_a_default():
    draft = edit(load_mandate("t1_bonds"), title=DELETE)
    assert compile_mandate("x", Canned(draft)).mandate["title"] == "Мандат клиента"


def test_non_object_mandate_is_reported():
    assert compile_mandate("x", Canned({"mandate": "oops"})).problems == ["Модель вернула не объект мандата."]


# --- разбор ответа модели ---


def test_parse_reply_variants():
    assert parse_reply('{"a": 1}') == {"a": 1}
    assert parse_reply('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_reply('Вот мандат:\n{"a": {"b": 2}}\nГотово.') == {"a": {"b": 2}}


@pytest.mark.parametrize("text", ["нет json", "[1, 2]", "{не json}", ""])
def test_parse_reply_rejects_garbage(text):
    with pytest.raises(ProviderError):
        parse_reply(text)


# --- OpenAICompatibleProvider (без сети: opener подменён) ---


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_opener(reply_content=None, raises=None, raw=None):
    captured = {}

    def opener(request, timeout):
        captured.update(request=request, timeout=timeout)
        if raises:
            raise raises
        body = raw if raw is not None else json.dumps({"choices": [{"message": {"content": reply_content}}]})
        return FakeResponse(body.encode("utf-8"))

    opener.captured = captured
    return opener


def test_openai_request_shape_and_reply():
    opener = fake_opener('{"questions": ["q"]}')
    provider = OpenAICompatibleProvider("http://localhost:8787/v1/", "KEY-123", "free-model", opener=opener, timeout=7)
    assert provider.complete("системный", "пользователь") == '{"questions": ["q"]}'
    request, timeout = opener.captured["request"], opener.captured["timeout"]
    assert request.full_url == "http://localhost:8787/v1/chat/completions" and request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer KEY-123" and timeout == 7
    body = json.loads(request.data)
    assert body["model"] == "free-model" and body["temperature"] == 0
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"] == [
        {"role": "system", "content": "системный"},
        {"role": "user", "content": "пользователь"},
    ]


def test_openai_compile_end_to_end():
    reply = json.dumps({"mandate": load_mandate("t1_bonds")}, ensure_ascii=False)
    provider = OpenAICompatibleProvider("http://x/v1", "k", "m", opener=fake_opener(reply))
    assert compile_mandate(T1_TEXT, provider).ok


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.HTTPError("http://x", 401, "Unauthorized", {}, None),
        urllib.error.URLError("refused"),
        TimeoutError(),
    ],
)
def test_openai_failures_become_provider_errors_without_the_key(failure):
    provider = OpenAICompatibleProvider("http://x/v1", "KEY-123", "m", opener=fake_opener(raises=failure))
    with pytest.raises(ProviderError) as exc:
        provider.complete("s", "u")
    assert "KEY-123" not in str(exc.value)


@pytest.mark.parametrize("raw", ["не json", "{}", '{"choices": []}'])
def test_openai_malformed_body_is_a_provider_error(raw):
    provider = OpenAICompatibleProvider("http://x/v1", "k", "m", opener=fake_opener(raw=raw))
    with pytest.raises(ProviderError):
        provider.complete("s", "u")


def test_provider_repr_hides_the_key():
    assert "KEY-123" not in repr(OpenAICompatibleProvider("http://x/v1", "KEY-123", "m"))


# --- системный промпт ---


def test_system_prompt_is_russian_and_describes_the_contract():
    for needle in ("mandat/v1", "questions", "allowed_classes", "max_orders_per_minute", "Не угадывай", "«ёлочки»"):
        assert needle in SYSTEM_PROMPT
    assert_no_long_dashes(SYSTEM_PROMPT)
    assert all(key in SYSTEM_PROMPT for key in TOP_KEYS if key not in ("schema", "title", "client_text"))


# --- компилятор + шлюз ---


def test_compiled_draft_goes_through_lint_into_signing():
    market = load_market()
    ok = compile_mandate(T1_TEXT, FixtureProvider()).mandate
    Gateway(ok, MockBroker(market))  # только предупреждение L_NO_DAILY - подписать можно
    free = compile_mandate(T2_TEXT, FixtureProvider()).mandate
    with pytest.raises(MandateRejected):
        Gateway(free, MockBroker(market))
