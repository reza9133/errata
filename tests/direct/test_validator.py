"""Validator equivalence: every stored or decisive value must match exactly."""

import json

import pytest

from tests.direct.support import (
    ARITH, D_FEE, Spec, deploy, finding_of, review, run_to_review, set_web, span_of,
    SUBJECT, SUBJECT_URL, verdict,
)

SPANS = span_of(D_FEE[0]) + span_of(D_FEE[1])
NO_SPANS = object()


def analysis(name="upheld", spans=None, reason="leader prose"):
    return {"kind": "analysis", "verdict": name, "reason": reason,
            "spans": list(SPANS if spans is None else spans)}


@pytest.fixture
def reviewed(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    spec = Spec("subject", actors[1], ARITH, D_FEE)
    run_to_review(contract, direct_vm, actors[0], [spec])
    review(contract, direct_vm, actors[0], spec)
    return contract, spec


def test_the_validator_accepts_a_matching_leader(direct_vm, reviewed):
    assert direct_vm.run_validator() is True


def test_different_prose_is_fine_but_nothing_else_is(direct_vm, reviewed):
    _, spec = reviewed
    direct_vm.mock_llm(spec.pattern(), verdict("upheld", "completely different words"))
    assert direct_vm.run_validator() is True


VERDICTS = ["upheld", "not_upheld", "unclear"]


@pytest.mark.parametrize("leader", VERDICTS)
@pytest.mark.parametrize("validator", VERDICTS)
def test_verdicts_must_match_exactly_with_no_adjacent_tolerance(
    direct_vm, reviewed, leader, validator
):
    _, spec = reviewed
    direct_vm.mock_llm(spec.pattern(), verdict(validator))
    assert direct_vm.run_validator(leader_result=analysis(leader)) is (leader == validator)


@pytest.mark.parametrize("spans", [
    [SPANS[0] + 1, SPANS[1] + 1, SPANS[2], SPANS[3]],      # shifted, same length
    [SPANS[0], SPANS[1], SPANS[2] + 2, SPANS[3] + 2],
    [SPANS[2], SPANS[3], SPANS[0], SPANS[1]],              # swapped anchors, same lengths? no
    [SPANS[0], SPANS[1] - 1, SPANS[2], SPANS[3]],          # wrong length
    [SPANS[0], SPANS[1], SPANS[0], SPANS[1]],              # overlapping
    [-1, SPANS[1], SPANS[2], SPANS[3]],
    [SPANS[0], SPANS[1], SPANS[2]],                        # too short
    [SPANS[0], SPANS[1], SPANS[2], SPANS[3], 0],           # too long
    [float(SPANS[0]), SPANS[1], SPANS[2], SPANS[3]],       # not an int
    [True, SPANS[1], SPANS[2], SPANS[3]],                  # bool is not an int here
    "0,1,2,3", NO_SPANS, {},
])
def test_a_leader_that_misreports_where_the_quotes_are_is_rejected(direct_vm, reviewed, spans):
    result = analysis()
    result["spans"] = None if spans is NO_SPANS else spans
    assert direct_vm.run_validator(leader_result=result) is False


@pytest.mark.parametrize("bad", [
    None, "analysis", 7, [], {}, {"kind": "nope"}, {"kind": "failure"},
    {"kind": "failure", "reason": ""}, {"kind": "failure", "reason": 5},
    {"kind": "failure", "reason": "x" * 65},
    {"kind": "evidence_issue", "reason": "made_up"}, {"kind": "evidence_issue"},
    {"kind": "analysis", "verdict": "maybe", "reason": "", "spans": SPANS},
    {"kind": "analysis", "reason": "", "spans": SPANS},
    {"kind": "analysis", "verdict": "upheld", "spans": SPANS},
    {"kind": "analysis", "verdict": "upheld", "reason": 3, "spans": SPANS},
    {"kind": "analysis", "verdict": ["upheld"], "reason": "", "spans": SPANS},
    {"kind": "analysis", "verdict": {"a": 1}, "reason": "", "spans": SPANS},
    {"kind": ["analysis"], "verdict": "upheld", "reason": "", "spans": SPANS},
    {"kind": {"x": 1}},
    {"kind": "evidence_issue", "reason": ["quote_a_not_found"]},
    {"kind": "evidence_issue", "reason": {"x": 1}},
    {"kind": "failure", "reason": ["subject_unavailable"]},
    {"kind": None, "verdict": "upheld", "reason": "", "spans": SPANS},
])
def test_malformed_leader_results_are_rejected(direct_vm, reviewed, bad):
    assert direct_vm.run_validator(leader_result=bad) is False


def test_a_leader_that_raised_is_rejected(direct_vm, reviewed):
    assert direct_vm.run_validator(leader_error=Exception("leader crashed")) is False


# ----------------------------------------------------------- failure classes


def test_a_subject_failure_is_confirmed_only_by_another_subject_failure(
    direct_vm, direct_deploy, actors
):
    contract = deploy(direct_vm, direct_deploy)
    spec = Spec("subject", actors[1], ARITH, D_FEE)
    run_to_review(contract, direct_vm, actors[0], [spec])
    set_web(direct_vm, SUBJECT_URL, {"status": 503, "body": b"down"})
    review(contract, direct_vm, actors[0], spec)
    assert finding_of(contract, actors[0], spec)["reason"] == "subject_unavailable"
    assert direct_vm.run_validator() is True                     # validator also cannot fetch
    set_web(direct_vm, SUBJECT_URL, {"status": 200, "body": SUBJECT})
    assert direct_vm.run_validator() is False                    # validator can fetch: leader lied
    direct_vm.mock_llm(spec.pattern(), "garbage")
    # A model failure is a DIFFERENT class (it charges the finder), so it cannot confirm a
    # subject failure.
    assert direct_vm.run_validator() is False


def test_a_model_failure_is_not_confirmed_by_a_subject_failure(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    spec = Spec("subject", actors[1], ARITH, D_FEE, answer="garbage")
    run_to_review(contract, direct_vm, actors[0], [spec])
    direct_vm.mock_llm(spec.pattern(), "garbage")
    review(contract, direct_vm, actors[0], spec)
    assert finding_of(contract, actors[0], spec)["reason"] == "malformed_or_unavailable_model_output"
    assert direct_vm.run_validator() is True
    set_web(direct_vm, SUBJECT_URL, {"status": 503, "body": b"down"})
    assert direct_vm.run_validator() is False
    set_web(direct_vm, SUBJECT_URL, {"status": 200, "body": SUBJECT})
    direct_vm.mock_llm(spec.pattern(), verdict("upheld"))
    assert direct_vm.run_validator() is False                    # validator got a real answer


def test_failure_wording_may_differ_inside_the_same_class(direct_vm, reviewed):
    assert direct_vm.run_validator(leader_result={"kind": "failure", "reason": "subject_unreadable"}) is False
    set_web(direct_vm, SUBJECT_URL, {"status": 503, "body": b""})
    assert direct_vm.run_validator(leader_result={"kind": "failure", "reason": "subject_unreadable"}) is True
    assert direct_vm.run_validator(leader_result={"kind": "failure", "reason": "subject_unavailable"}) is True


# --------------------------------------------------------- evidence agreement


def test_evidence_issues_must_agree_on_the_exact_reason(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    spec = Spec("subject", actors[1], ARITH, (D_FEE[0], "this passage does not exist"))
    run_to_review(contract, direct_vm, actors[0], [spec])
    review(contract, direct_vm, actors[0], spec)
    assert finding_of(contract, actors[0], spec)["reason"] == "evidence:quote_b_not_found"
    assert direct_vm.run_validator() is True
    for other in ("quote_a_not_found", "quote_a_ambiguous", "quote_b_ambiguous", "quotes_overlap"):
        assert direct_vm.run_validator(
            leader_result={"kind": "evidence_issue", "reason": other}) is False
    assert direct_vm.run_validator(leader_result=analysis()) is False


def test_an_evidence_claim_is_rejected_when_the_validator_finds_the_quotes(direct_vm, reviewed):
    for reason in ("quote_a_not_found", "quote_b_not_found", "quote_a_ambiguous",
                   "quote_b_ambiguous", "quotes_overlap"):
        assert direct_vm.run_validator(
            leader_result={"kind": "evidence_issue", "reason": reason}) is False


# --------------------------------------------------------- validator-side faults


def test_the_validator_rejects_an_analysis_it_cannot_reproduce(direct_vm, reviewed):
    _, spec = reviewed
    set_web(direct_vm, SUBJECT_URL, {"status": 503, "body": b""})
    assert direct_vm.run_validator() is False
    set_web(direct_vm, SUBJECT_URL, {"status": 200, "body": SUBJECT.replace(b"120", b"121")})
    assert direct_vm.run_validator() is False  # hash mismatch: not the pinned document
    set_web(direct_vm, SUBJECT_URL, {"status": 200, "body": SUBJECT})
    direct_vm.mock_llm(spec.pattern(), "not json at all")
    assert direct_vm.run_validator() is False
    direct_vm.mock_llm(spec.pattern(), json.dumps({"verdict": "bogus"}))
    assert direct_vm.run_validator() is False
    direct_vm.mock_llm(spec.pattern(), verdict("upheld"))
    assert direct_vm.run_validator() is True
