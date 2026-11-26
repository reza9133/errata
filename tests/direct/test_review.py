"""Review: evidence checks in code, one bounded model call, retries and context widening."""

import re

import pytest

from tests.direct.support import (
    slot_of,
    AMBIGUOUS, ARITH, COMMIT_BY, CONTRA, D_FEE, D_MEETING, D_MEMBER, DEFN, MISSING, NOW_TS,
    OVERLAPPING, REVEAL_BY, REVIEW_BY, SUBJECT, SUBJECT_URL, Spec, addr, at,
    digest, finalize, finding_of, llm_pattern, open_bounty, review, run_to_review, serve, set_web, span_of,
    verdict,
)

BAD_BODY = {"status": 503, "body": b"down"}


def _one(direct_vm, direct_deploy, actors, **spec_kwargs):
    from tests.direct.support import deploy
    contract = deploy(direct_vm, direct_deploy)
    spec = Spec("subject", actors[1], spec_kwargs.pop("kind", ARITH),
                spec_kwargs.pop("pair", D_FEE), **spec_kwargs)
    run_to_review(contract, direct_vm, actors[0], [spec])
    return contract, spec


def _state(contract, actors, spec):
    return finding_of(contract, actors[0], spec)


# ------------------------------------------------------------------ verdicts


@pytest.mark.parametrize("answer,status", [("upheld", "upheld"), ("not_upheld", "not_upheld")])
def test_a_decisive_verdict_is_final_and_cannot_be_rerolled(
    direct_vm, direct_deploy, actors, answer, status
):
    contract, spec = _one(direct_vm, direct_deploy, actors, answer=answer)
    review(contract, direct_vm, actors[0], spec)
    state = _state(contract, actors, spec)
    assert state["status"] == status
    assert state["attempts"] == 1 and state["review_calls"] == 1
    assert state["reason"] == ""
    assert [state["a_start"], state["a_end"]] == span_of(D_FEE[0])
    assert [state["b_start"], state["b_end"]] == span_of(D_FEE[1])
    direct_vm.warp(at(REVEAL_BY + 7200))
    with direct_vm.expect_revert():
        review(contract, direct_vm, actors[0], spec)
    direct_vm.mock_llm(spec.pattern(), verdict("upheld" if status == "not_upheld" else "not_upheld"))
    with direct_vm.expect_revert():
        review(contract, direct_vm, actors[0], spec, caller=spec.who)
    assert _state(contract, actors, spec)["status"] == status


def test_verdict_names_are_normalized_and_prose_is_stored_trimmed(direct_vm, direct_deploy, actors):
    contract, spec = _one(direct_vm, direct_deploy, actors,
                          answer={"verdict": "  Not-Upheld ", "reason": "  too   much\nspace " + "x" * 400,
                                  "extra": "ignored"})
    review(contract, direct_vm, actors[0], spec)
    state = _state(contract, actors, spec)
    assert state["status"] == "not_upheld"
    assert len(state["rationale"]) == 240
    assert "\n" not in state["rationale"] and "  " not in state["rationale"]


def test_a_missing_reason_is_tolerated(direct_vm, direct_deploy, actors):
    contract, spec = _one(direct_vm, direct_deploy, actors, answer={"verdict": "upheld"})
    review(contract, direct_vm, actors[0], spec)
    assert _state(contract, actors, spec)["status"] == "upheld"
    assert _state(contract, actors, spec)["rationale"] == ""


@pytest.mark.parametrize("decorated,status", [
    ("Upheld.", "upheld"), ("UPHELD!", "upheld"), ("not upheld.", "not_upheld"),
    ("`not_upheld`", "not_upheld"), (" Unclear ", "revealed"), ("not-upheld", "not_upheld"),
])
def test_decorated_verdict_words_are_understood(direct_vm, direct_deploy, actors, decorated, status):
    contract, spec = _one(direct_vm, direct_deploy, actors)
    direct_vm.mock_llm(spec.pattern(), {"verdict": decorated, "reason": "x"})
    review(contract, direct_vm, actors[0], spec)
    state = _state(contract, actors, spec)
    assert state["status"] == status
    assert state["reason"] != "malformed_or_unavailable_model_output"


@pytest.mark.parametrize("answer", [
    "not json at all", {"reason": "no verdict"}, {"verdict": "maybe"}, {"verdict": 7},
    {"verdict": None}, {"verdict": "upheld", "reason": 5}, ["upheld"], "upheld",
])
def test_unusable_model_output_is_a_charged_retryable_failure(
    direct_vm, direct_deploy, actors, answer
):
    import json
    contract, spec = _one(direct_vm, direct_deploy, actors)
    direct_vm.mock_llm(spec.pattern(), answer if isinstance(answer, str) else json.dumps(answer))
    review(contract, direct_vm, actors[0], spec)
    state = _state(contract, actors, spec)
    assert state["status"] == "revealed"
    assert state["reason"] == "malformed_or_unavailable_model_output"
    assert state["review_calls"] == 1 and state["attempts"] == 0
    assert state["retry_at"] == REVEAL_BY + 60
    assert state["a_end"] == 0  # nothing was recorded


def test_model_failures_use_up_the_review_budget_with_growing_cooldowns(
    direct_vm, direct_deploy, actors
):
    contract, spec = _one(direct_vm, direct_deploy, actors)
    direct_vm.mock_llm(spec.pattern(), "garbage")
    clock = REVEAL_BY
    delays = []
    for call in range(1, 9):
        direct_vm.warp(at(clock))
        review(contract, direct_vm, actors[0], spec)
        state = _state(contract, actors, spec)
        assert state["review_calls"] == call
        delays.append(state["retry_at"] - clock)
        clock = state["retry_at"]
    assert delays == [60, 120, 240, 480, 960, 1920, 3600, 3600]
    direct_vm.warp(at(clock))
    with direct_vm.expect_revert():
        review(contract, direct_vm, actors[0], spec)  # budget exhausted
    # Exhausted findings no longer block finalization.
    finalize(contract, direct_vm, actors[5], actors[0])
    assert _state(contract, actors, spec)["outcome"] == "unresolved_bond_refunded"


def test_the_cooldown_blocks_an_immediate_second_call(direct_vm, direct_deploy, actors):
    contract, spec = _one(direct_vm, direct_deploy, actors)
    direct_vm.mock_llm(spec.pattern(), "garbage")
    review(contract, direct_vm, actors[0], spec)
    direct_vm.warp(at(REVEAL_BY + 59))
    with direct_vm.expect_revert():
        review(contract, direct_vm, actors[0], spec)
    direct_vm.mock_llm(spec.pattern(), verdict("upheld"))
    direct_vm.warp(at(REVEAL_BY + 60))
    review(contract, direct_vm, actors[0], spec, caller=spec.who)
    assert _state(contract, actors, spec)["status"] == "upheld"


# ------------------------------------------------------- unclear widens context


def test_unclear_widens_the_context_until_a_decision_is_reached(direct_vm, direct_deploy, actors):
    contract, spec = _one(direct_vm, direct_deploy, actors)
    direct_vm._llm_mocks.clear()  # drop the generic mock; first match wins
    for radius, answer in ((300, "unclear"), (900, "unclear"), (2000, "upheld")):
        direct_vm.mock_llm(llm_pattern(spec.kind, *spec.pair, radius=radius), verdict(answer))
    clock = REVEAL_BY
    seen = []
    for expected_status, radius in (("revealed", 300), ("revealed", 900), ("upheld", 2000)):
        direct_vm.warp(at(clock))
        review(contract, direct_vm, actors[0], spec)
        state = _state(contract, actors, spec)
        assert state["status"] == expected_status
        seen.append(state["attempts"])
        if expected_status == "revealed":
            assert state["reason"] == f"unclear_at_radius_{radius}"
        clock = state["retry_at"]
    assert seen == [1, 2, 3]


def test_three_unclear_answers_exhaust_the_finding_without_a_reward(direct_vm, direct_deploy, actors):
    contract, spec = _one(direct_vm, direct_deploy, actors, answer="unclear")
    clock = REVEAL_BY
    for _ in range(3):
        direct_vm.warp(at(clock))
        review(contract, direct_vm, actors[0], spec)
        clock = _state(contract, actors, spec)["retry_at"]
    state = _state(contract, actors, spec)
    assert state["status"] == "revealed" and state["attempts"] == 3
    direct_vm.warp(at(clock))
    with direct_vm.expect_revert():
        review(contract, direct_vm, actors[0], spec)
    finalize(contract, direct_vm, actors[7], actors[0])  # allowed long before REVIEW_BY
    final = _state(contract, actors, spec)
    assert final["outcome"] == "unresolved_bond_refunded"
    assert final["reward_wei"] == 0


def test_the_radius_actually_changes_how_much_text_the_model_sees(direct_vm, direct_deploy, actors):
    from tests.direct.support import capture_prompts
    contract, spec = _one(direct_vm, direct_deploy, actors, pair=D_MEMBER, kind=DEFN,
                          answer="unclear")
    prompts = capture_prompts(direct_vm, "unclear")
    clock = REVEAL_BY
    for _ in range(3):
        direct_vm.warp(at(clock))
        review(contract, direct_vm, actors[0], spec)
        clock = _state(contract, actors, spec)["retry_at"]
    assert [bool(re.search(rf"cut off at about {r} characters", p)) for p, r in
            zip(prompts, (300, 900, 2000))] == [True, True, True]
    lengths = [len(p) for p in prompts]
    assert lengths[0] < lengths[1] <= lengths[2]


# ------------------------------------------------------------- subject sourcing


def test_an_unreachable_subject_never_charges_the_finder(direct_vm, direct_deploy, actors):
    contract, spec = _one(direct_vm, direct_deploy, actors)
    set_web(direct_vm, SUBJECT_URL, BAD_BODY)
    review(contract, direct_vm, actors[0], spec)
    state = _state(contract, actors, spec)
    assert state["status"] == "revealed"
    assert state["reason"] == "subject_unavailable"
    assert state["review_calls"] == 0 and state["attempts"] == 0
    assert state["retry_at"] == REVEAL_BY + 60
    direct_vm.warp(at(REVEAL_BY + 30))
    with direct_vm.expect_revert():
        review(contract, direct_vm, actors[0], spec)
    # Any number of such failures leaves the budget intact: the publisher's host is not the finder's fault.
    clock = REVEAL_BY + 60
    for _ in range(12):
        direct_vm.warp(at(clock))
        review(contract, direct_vm, actors[0], spec, caller=spec.who)
        clock += 60
    assert _state(contract, actors, spec)["review_calls"] == 0
    set_web(direct_vm, SUBJECT_URL, {"status": 200, "body": SUBJECT})
    direct_vm.warp(at(clock))
    review(contract, direct_vm, actors[0], spec)
    assert _state(contract, actors, spec)["status"] == "upheld"


@pytest.mark.parametrize("response", [
    {"status": 404, "body": b"gone"}, {"status": 301, "body": b""}, {"status": 199, "body": SUBJECT},
    {"status": 200, "body": b""}, {"status": 200, "body": b"swapped document bytes"},
    {"status": 200, "body": SUBJECT + b" "}, {"status": 200, "body": SUBJECT[:-1]},
    {"status": 200, "body": b"x" * 100_001},
])
def test_every_unusable_subject_response_is_the_same_retryable_failure(
    direct_vm, direct_deploy, actors, response
):
    contract, spec = _one(direct_vm, direct_deploy, actors)
    set_web(direct_vm, SUBJECT_URL, response)
    review(contract, direct_vm, actors[0], spec)
    state = _state(contract, actors, spec)
    assert state["status"] == "revealed" and state["reason"] == "subject_unavailable"
    assert state["review_calls"] == 0


def test_an_unmocked_subject_url_is_also_just_unavailable(direct_vm, direct_deploy, actors):
    contract, spec = _one(direct_vm, direct_deploy, actors)
    direct_vm._web_mocks.clear()
    review(contract, direct_vm, actors[0], spec)
    assert _state(contract, actors, spec)["reason"] == "subject_unavailable"


def test_a_subject_that_is_not_utf8_is_unreadable_not_the_finders_fault(
    direct_vm, direct_deploy, actors
):
    from tests.direct.support import deploy
    contract = deploy(direct_vm, direct_deploy)
    binary = b"\xff\xfe Section 1. not text \x80\x81"
    spec = Spec("subject", actors[1], ARITH, D_FEE)
    run_to_review(contract, direct_vm, actors[0], [spec], subject_hash=digest(binary))
    set_web(direct_vm, SUBJECT_URL, {"status": 200, "body": binary})
    review(contract, direct_vm, actors[0], spec)
    state = _state(contract, actors, spec)
    assert state["reason"] == "subject_unreadable" and state["status"] == "revealed"
    assert state["review_calls"] == 0


def test_a_whitespace_only_subject_is_unreadable(direct_vm, direct_deploy, actors):
    from tests.direct.support import deploy
    contract = deploy(direct_vm, direct_deploy)
    blank = b" \n\t \r\n "
    spec = Spec("subject", actors[1], ARITH, D_FEE)
    run_to_review(contract, direct_vm, actors[0], [spec], subject_hash=digest(blank))
    set_web(direct_vm, SUBJECT_URL, {"status": 200, "body": blank})
    review(contract, direct_vm, actors[0], spec)
    assert _state(contract, actors, spec)["reason"] == "subject_unreadable"


MIRROR = "https://mirror.example.org/handbook-copy.md"


def test_a_finder_mirror_rescues_a_dead_primary_and_the_hash_keeps_it_honest(
    direct_vm, direct_deploy, actors
):
    from tests.direct.support import deploy
    contract = deploy(direct_vm, direct_deploy)
    spec = Spec("mirrored", actors[1], ARITH, D_FEE, mirror=MIRROR)
    run_to_review(contract, direct_vm, actors[0], [spec])
    set_web(direct_vm, SUBJECT_URL, BAD_BODY)
    set_web(direct_vm, MIRROR, {"status": 200, "body": SUBJECT})
    review(contract, direct_vm, actors[0], spec)
    assert _state(contract, actors, spec)["status"] == "upheld"


def test_a_tampered_primary_is_skipped_in_favour_of_the_honest_mirror(direct_vm, direct_deploy, actors):
    from tests.direct.support import deploy
    contract = deploy(direct_vm, direct_deploy)
    spec = Spec("mirrored", actors[1], ARITH, D_FEE, mirror=MIRROR)
    run_to_review(contract, direct_vm, actors[0], [spec])
    edited = SUBJECT.replace(b"120 dollars", b"100 dollars")  # the publisher quietly "fixes" the text
    set_web(direct_vm, SUBJECT_URL, {"status": 200, "body": edited})
    set_web(direct_vm, MIRROR, {"status": 200, "body": SUBJECT})
    review(contract, direct_vm, actors[0], spec)
    assert _state(contract, actors, spec)["status"] == "upheld"


def test_a_lying_mirror_cannot_inject_different_text(direct_vm, direct_deploy, actors):
    from tests.direct.support import deploy
    contract = deploy(direct_vm, direct_deploy)
    spec = Spec("liar", actors[1], ARITH, D_FEE, mirror=MIRROR)
    run_to_review(contract, direct_vm, actors[0], [spec])
    forged = SUBJECT.replace(b"120 dollars", b"100 dollars")
    set_web(direct_vm, SUBJECT_URL, BAD_BODY)
    set_web(direct_vm, MIRROR, {"status": 200, "body": forged})
    review(contract, direct_vm, actors[0], spec)
    state = _state(contract, actors, spec)
    assert state["status"] == "revealed" and state["reason"] == "subject_unavailable"


def test_a_mirror_equal_to_the_primary_changes_nothing(direct_vm, direct_deploy, actors):
    from tests.direct.support import deploy
    contract = deploy(direct_vm, direct_deploy)
    spec = Spec("same", actors[1], ARITH, D_FEE, mirror=SUBJECT_URL)
    run_to_review(contract, direct_vm, actors[0], [spec])
    review(contract, direct_vm, actors[0], spec)
    assert _state(contract, actors, spec)["status"] == "upheld"


def test_a_subject_at_the_size_limit_is_accepted_and_spans_are_in_collapsed_text(
    direct_vm, direct_deploy, actors
):
    from tests.direct.support import deploy
    contract = deploy(direct_vm, direct_deploy)
    padding = b"lorem ipsum "
    big = SUBJECT + padding * ((100_000 - len(SUBJECT)) // len(padding))
    big += b"x" * (100_000 - len(big))
    assert len(big) == 100_000
    spec = Spec("big", actors[1], ARITH, D_FEE)
    run_to_review(contract, direct_vm, actors[0], [spec], subject_hash=digest(big))
    set_web(direct_vm, SUBJECT_URL, {"status": 200, "body": big})
    review(contract, direct_vm, actors[0], spec)
    state = _state(contract, actors, spec)
    assert state["status"] == "upheld"
    assert [state["a_start"], state["a_end"]] == span_of(D_FEE[0])


def test_a_subject_one_byte_over_the_limit_is_unusable_even_with_a_matching_hash(
    direct_vm, direct_deploy, actors
):
    from tests.direct.support import deploy
    contract = deploy(direct_vm, direct_deploy)
    big = SUBJECT + b"x" * (100_001 - len(SUBJECT))
    spec = Spec("big", actors[1], ARITH, D_FEE)
    run_to_review(contract, direct_vm, actors[0], [spec], subject_hash=digest(big))
    set_web(direct_vm, SUBJECT_URL, {"status": 200, "body": big})
    review(contract, direct_vm, actors[0], spec)
    assert _state(contract, actors, spec)["reason"] == "subject_unavailable"


# ------------------------------------------------------------ evidence in code


@pytest.mark.parametrize("pair,reason", [
    (MISSING, "quote_a_not_found"),
    ((D_FEE[0], "this passage does not exist"), "quote_b_not_found"),
    (("neither passage exists", "nor does this one"), "quote_a_not_found"),
    (AMBIGUOUS, "quote_a_ambiguous"),
    ((D_MEETING[0], "annual fee"), "quote_b_ambiguous"),
    (("annual fee", "this passage does not exist"), "quote_b_not_found"),
    (OVERLAPPING, "quotes_overlap"),
    ((D_FEE[0], "THE ANNUAL FEE IS 120 DOLLARS"), "quote_b_not_found"),
])
def test_bad_quotes_disqualify_the_finder_for_exactly_the_found_reason(
    direct_vm, direct_deploy, actors, pair, reason
):
    contract, spec = _one(direct_vm, direct_deploy, actors, pair=pair)
    review(contract, direct_vm, actors[0], spec)
    state = _state(contract, actors, spec)
    assert state["status"] == "disqualified"
    assert state["reason"] == "evidence:" + reason
    assert state["review_calls"] == 1 and state["attempts"] == 0
    assert state["a_end"] == 0
    direct_vm.warp(at(REVEAL_BY + 7200))
    with direct_vm.expect_revert():
        review(contract, direct_vm, actors[0], spec)


def test_evidence_is_checked_before_the_model_is_ever_called(direct_vm, direct_deploy, actors):
    from tests.direct.support import capture_prompts
    contract, spec = _one(direct_vm, direct_deploy, actors, pair=MISSING)
    prompts = capture_prompts(direct_vm)
    review(contract, direct_vm, actors[0], spec)
    assert prompts == []


def test_unicode_quotes_work_and_normalization_differences_do_not(direct_vm, direct_deploy, actors):
    from tests.direct.support import deploy
    import unicodedata
    contract = deploy(direct_vm, direct_deploy)
    nfc = "Section 1. The café charges 5 € per person and 3 € per child under 12."
    document = (nfc + " Section 2. The café sells a family ticket for 20 €.").encode()
    pair_ok = ("The café charges 5 € per person", "a family ticket for 20 €")
    pair_nfd = (unicodedata.normalize("NFD", pair_ok[0]), pair_ok[1])
    good = Spec("good", actors[1], ARITH, pair_ok)
    bad = Spec("bad", actors[2], ARITH, pair_nfd)
    run_to_review(contract, direct_vm, actors[0], [good, bad], subject_hash=digest(document))
    set_web(direct_vm, SUBJECT_URL, {"status": 200, "body": document})
    review(contract, direct_vm, actors[0], good)
    review(contract, direct_vm, actors[0], bad)
    assert _state(contract, actors, good)["status"] == "upheld"
    assert _state(contract, actors, bad)["reason"] == "evidence:quote_a_not_found"


# --------------------------------------------------------------- who and when


def test_only_the_publisher_or_the_finder_may_request_a_review(direct_vm, direct_deploy, actors):
    from tests.direct.support import deploy
    contract = deploy(direct_vm, direct_deploy)
    victim = Spec("victim", actors[1], ARITH, D_FEE)
    rival = Spec("rival", actors[2], CONTRA, D_MEETING)
    run_to_review(contract, direct_vm, actors[0], [victim, rival])
    for outsider in (actors[3], rival.who):
        with direct_vm.expect_revert():
            review(contract, direct_vm, actors[0], victim, caller=outsider)
    assert _state(contract, actors, victim)["review_calls"] == 0
    review(contract, direct_vm, actors[0], victim, caller=victim.who)
    review(contract, direct_vm, actors[0], rival)
    assert _state(contract, actors, victim)["status"] == "upheld"
    assert _state(contract, actors, rival)["status"] == "upheld"


def test_review_windows_and_unrevealed_findings(direct_vm, direct_deploy, actors):
    from tests.direct.support import commit, deploy, reveal
    contract = deploy(direct_vm, direct_deploy)
    shown = Spec("shown", actors[1], ARITH, D_FEE)
    hidden = Spec("hidden", actors[2], CONTRA, D_MEETING)
    open_bounty(contract, direct_vm, actors[0])
    direct_vm.warp(at(NOW_TS + 60))
    commit(contract, direct_vm, actors[0], shown)
    commit(contract, direct_vm, actors[0], hidden)
    direct_vm.warp(at(COMMIT_BY))
    reveal(contract, direct_vm, actors[0], shown)
    serve(direct_vm, [shown, hidden])
    direct_vm.warp(at(REVEAL_BY - 1))
    with direct_vm.expect_revert():
        review(contract, direct_vm, actors[0], shown)  # still in the reveal window
    direct_vm.warp(at(REVEAL_BY))
    with direct_vm.expect_revert():
        review(contract, direct_vm, actors[0], hidden)  # never revealed
    with direct_vm.expect_revert():
        contract.review_finding(addr(actors[0]), "B-001", 9)  # no such finding
    with direct_vm.expect_revert():
        contract.review_finding(addr(actors[0]), "NOPE", slot_of(shown, actors[0]))
    direct_vm.warp(at(REVIEW_BY - 1))
    review(contract, direct_vm, actors[0], shown)
    assert _state(contract, actors, shown)["status"] == "upheld"


def test_review_is_closed_once_the_window_ends_or_the_bounty_is_finalized(
    direct_vm, direct_deploy, actors
):
    contract, spec = _one(direct_vm, direct_deploy, actors)
    direct_vm.warp(at(REVIEW_BY))
    with direct_vm.expect_revert():
        review(contract, direct_vm, actors[0], spec)
    finalize(contract, direct_vm, actors[3], actors[0])
    with direct_vm.expect_revert():
        review(contract, direct_vm, actors[0], spec)


def test_a_consensus_level_exception_is_charged_not_fatal(direct_vm, direct_deploy, actors, monkeypatch):
    """If the nondeterministic block itself raises, the call is recorded and charged."""
    contract, spec = _one(direct_vm, direct_deploy, actors)
    direct_vm._llm_mocks.clear()  # no mock at all: the model call raises MockNotFoundError
    review(contract, direct_vm, actors[0], spec)
    state = _state(contract, actors, spec)
    assert state["status"] == "revealed"
    assert state["review_calls"] == 1
    assert state["reason"] in ("malformed_or_unavailable_model_output", "consensus_execution_failure")
