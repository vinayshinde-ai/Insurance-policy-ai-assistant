import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import assistant as a  # noqa: E402


@pytest.mark.parametrize("name", ["john_doe", "john.smith", "A-1", "x" * 64])
def test_valid_usernames(name):
    assert a.policy_key(name) == f"{name}.txt"


@pytest.mark.parametrize("name", ["", "../etc/passwd", "a/b", "john doe", "x" * 65, None])
def test_invalid_usernames_rejected(name):
    with pytest.raises(ValueError):
        a.policy_key(name)


@pytest.mark.parametrize("uri,label", [
    ("s3://b/car_insurance_policy_booklet_11_2023.pdf", "Car Insurance Policy Booklet"),
    ("s3://b/keycare_policy_booklet.pdf", "Keycare Policy Booklet"),
    ("s3://b/AnyCompany Motor Insurance FAQs.pdf", "AnyCompany Motor Insurance FAQs"),
])
def test_source_label(uri, label):
    assert a.source_label(uri) == label


def test_parse_retrieval_and_citations():
    results = [
        {"content": {"text": "Windscreen cover"}, "location": {"s3Location": {"uri": "s3://b/keycare_policy_booklet.pdf"}},
         "metadata": {a.PAGE_KEY: 4.0}},
        {"content": {"text": "Same doc"}, "location": {"s3Location": {"uri": "s3://b/keycare_policy_booklet.pdf"}},
         "metadata": {a.PAGE_KEY: 4}},
        {"content": {"text": ""}},
        {"content": {"text": "No page"}, "location": {"s3Location": {"uri": "s3://b/important_information.pdf"}}},
    ]
    sources = a.parse_retrieval_results(results)
    assert len(sources) == 3
    assert a.citations(sources) == ["Keycare Policy Booklet (page 4)", "Important Information"]
    grounding = a.format_grounding("POLICY", sources)
    assert "<customer_policy>\nPOLICY\n</customer_policy>" in grounding
    assert 'page="4"' in grounding
    assert "<company_contact>" not in grounding
    assert "help@example.com" in a.format_grounding("POLICY", sources, "help@example.com")


def test_build_messages_alternates_and_tags_guard_content():
    history = [
        {"role": "assistant", "content": "greeting"},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "dangling"},
    ]
    msgs = a.build_messages(history, "GROUND", "q2", max_turns=6)
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    last = msgs[-1]["content"]
    assert last[0]["guardContent"]["text"] == {"text": "GROUND", "qualifiers": ["grounding_source"]}
    assert last[1]["guardContent"]["text"] == {"text": "q2", "qualifiers": ["query", "guard_content"]}


def test_build_messages_limits_history():
    history = []
    for i in range(20):
        history += [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": f"a{i}"}]
    msgs = a.build_messages(history, "G", "now", max_turns=2)
    assert len(msgs) == 5
    assert msgs[0]["content"][0]["text"] == "q18"


def test_stream_parsing():
    events = [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockDelta": {"delta": {"text": "Hello "}}},
        {"contentBlockDelta": {"delta": {"text": "there"}}},
        {"messageStop": {"stopReason": "guardrail_intervened"}},
        {"metadata": {"usage": {"inputTokens": 10, "outputTokens": 2}}},
    ]
    result = a.StreamResult()
    assert list(a.iter_converse_stream(events, result)) == ["Hello ", "there"]
    assert result.text == "Hello there"
    assert result.guardrail_intervened
    assert result.usage["outputTokens"] == 2


def test_output_block_detected_from_trace():
    events = [
        {"contentBlockDelta": {"delta": {"text": "blocked message"}}},
        {"messageStop": {"stopReason": "end_turn"}},
        {"metadata": {"usage": {}, "trace": {"guardrail": {"outputAssessments": {"g": [
            {"contextualGroundingPolicy": {"filters": [{"type": "GROUNDING", "action": "BLOCKED"}]}}]}}}}},
    ]
    result = a.StreamResult()
    list(a.iter_converse_stream(events, result))
    assert result.guardrail_intervened


def test_no_block_when_trace_clean():
    events = [
        {"messageStop": {"stopReason": "end_turn"}},
        {"metadata": {"trace": {"guardrail": {"outputAssessments": {"g": [
            {"contextualGroundingPolicy": {"filters": [{"type": "GROUNDING", "action": "NONE"}]}}]}}}}},
    ]
    result = a.StreamResult()
    list(a.iter_converse_stream(events, result))
    assert not result.guardrail_intervened


def test_clean_question():
    assert a.clean_question("  hi\x00 ") == ("hi", None)
    assert a.clean_question("   ")[1]
    assert a.clean_question("x" * (a.MAX_QUESTION_CHARS + 1))[1]


def test_settings_from_env():
    env = {k: "v" for k in ["AWS_REGION", "KB_ID", "GUARDRAIL_ID", "GUARDRAIL_VERSION", "MODEL_ID",
                            "POLICY_BUCKET", "CHAT_TABLE", "USER_POOL_ID", "APP_CLIENT_ID"]}
    env["MAX_TOKENS"] = "512"
    assert a.Settings.from_env(env).max_tokens == 512
    del env["KB_ID"]
    with pytest.raises(RuntimeError, match="KB_ID"):
        a.Settings.from_env(env)


GOOD_PW = "Str0ng!Password"


@pytest.mark.parametrize("pw,ok", [(GOOD_PW, True), ("Short1!a", False), ("alllowercase1!x", False),
                                   ("NoDigitsHere!!", False), ("NoSymbols12345", False)])
def test_password_error(pw, ok):
    assert (a.password_error(pw) is None) == ok


def test_validate_signup():
    assert a.validate_signup("jane_roe", "j@example.com", GOOD_PW, GOOD_PW) is None
    assert "Username" in a.validate_signup("Jane Roe", "j@example.com", GOOD_PW, GOOD_PW)
    assert "email" in a.validate_signup("jane_roe", "nope", GOOD_PW, GOOD_PW)
    assert "match" in a.validate_signup("jane_roe", "j@example.com", GOOD_PW, GOOD_PW + "x")


def test_auth_error_message():
    assert "12 characters" in a.auth_error_message("InvalidPasswordException")
    assert a.auth_error_message("UserLambdaValidationException") == a.USERNAME_UNAVAILABLE
    assert a.auth_error_message("Whatever") == "Something went wrong. Please try again."
