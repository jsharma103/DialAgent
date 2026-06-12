import importlib

import pytest

ev = importlib.import_module("eval")


# --- norm_text ---

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Yes, we're in-network!", "yes were innetwork"),
        ("  Multiple   spaces and CAPS  ", "multiple spaces and caps"),
        ("$189 (cash)", "189 cash"),
    ],
)
def test_norm_text(raw, expected):
    assert ev.norm_text(raw) == expected


# --- run_checks ---

def _result(answers):
    return {"answers": answers, "confidence": "high", "task_completed": True}


def test_evidence_grounded_passes_on_verbatim_quote():
    turns = [
        {"role": "user", "text": "Yes, we're in-network with Delta Dental PPO."},
        {"role": "agent", "text": "Thanks!"},
    ]
    result = _result({"takes_ppo": {"value": True, "evidence": "we're in-network with Delta Dental PPO"}})
    checks = {c["name"]: c for c in ev.run_checks(turns, result, [])}
    assert checks["evidence_grounded"]["pass"]


def test_evidence_grounded_fails_on_paraphrase():
    turns = [{"role": "user", "text": "Yes, we take that plan."}]
    result = _result({"takes_ppo": {"value": True, "evidence": "we are in network with Delta"}})
    checks = {c["name"]: c for c in ev.run_checks(turns, result, [])}
    assert not checks["evidence_grounded"]["pass"]
    assert "takes_ppo" in checks["evidence_grounded"]["detail"]


def test_evidence_grounded_null_evidence_is_fine():
    turns = [{"role": "user", "text": "We don't quote prices."}]
    result = _result({"price": {"value": None, "evidence": None}})
    checks = {c["name"]: c for c in ev.run_checks(turns, result, [])}
    assert checks["evidence_grounded"]["pass"]


def test_dtmf_silent_reflects_violations():
    checks = {c["name"]: c for c in ev.run_checks([], _result({}), ["spoke while pressing"])}
    assert not checks["dtmf_silent"]["pass"]
    checks_ok = {c["name"]: c for c in ev.run_checks([], _result({}), [])}
    assert checks_ok["dtmf_silent"]["pass"]


def test_end_call_terminal():
    bad = [
        {"role": "agent", "text": "[ENDED CALL: done]"},
        {"role": "agent", "text": "oh wait, one more thing"},
    ]
    checks = {c["name"]: c for c in ev.run_checks(bad, _result({}), [])}
    assert not checks["end_call_terminal"]["pass"]

    good = [
        {"role": "agent", "text": "Goodbye!"},
        {"role": "agent", "text": "[ENDED CALL: done]"},
        {"role": "user", "text": "[CALL DISCONNECTED]"},
    ]
    checks = {c["name"]: c for c in ev.run_checks(good, _result({}), [])}
    assert checks["end_call_terminal"]["pass"]


# --- fixture comparison helpers ---

@pytest.mark.parametrize(
    "expected,got,match",
    [
        (None, None, True),
        (None, False, False),
        (True, True, True),
        (True, "yes", True),
        (True, "no", False),
        (False, "No", True),
        (False, None, False),
        (120, "$120", True),
        (120, 120.0, True),
        (120, "about 120 dollars", False),
        ("ppo", " PPO ", True),
    ],
)
def test_values_match(expected, got, match):
    assert ev.values_match(expected, got) is match


def test_match_answer_key_exact_and_overlap():
    keys = ["accepts_delta_dental_ppo", "saturday_availability"]
    assert ev.match_answer_key("saturday_availability", keys) == "saturday_availability"
    assert ev.match_answer_key("takes_delta_dental_ppo", keys) == "accepts_delta_dental_ppo"
    assert ev.match_answer_key("parking_situation", keys) is None


# --- summarize: overall requires rubric AND answers AND checks AND result_checks ---

def _grade(rubric_ok=True, answers_ok=True, rchecks_ok=True):
    return {
        "rubric": {"stayed_on_task": {"verdict": "pass" if rubric_ok else "fail", "rationale": ""}},
        "answer_correctness": {"k": {"expected": 1, "got": 1, "match": answers_ok, "rationale": ""}},
        "result_checks": {"check_1": {"verdict": "pass" if rchecks_ok else "fail", "rationale": ""}},
    }


def _checks(ok=True):
    return [{"name": "dtmf_silent", "pass": ok, "detail": ""}]


def test_summarize_all_green():
    s = ev.summarize(_grade(), _checks())
    assert s["overall"]


@pytest.mark.parametrize(
    "grade,checks",
    [
        (_grade(rubric_ok=False), _checks()),
        (_grade(answers_ok=False), _checks()),
        (_grade(rchecks_ok=False), _checks()),
        (_grade(), _checks(ok=False)),
    ],
)
def test_summarize_any_failure_fails_overall(grade, checks):
    assert not ev.summarize(grade, checks)["overall"]
