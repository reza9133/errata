"""The published errata sheet (get_errata) and the standing view after finalization."""

from tests.direct.support import (
    ARITH, BOND, BOUNTY_ID, CONTRA, D_FEE, D_FEE_VARIANT, D_LATE, D_MEETING, D_MEMBER, DEFN, MISSING,
    MIN_BUDGET, OK_HALL, Spec, addr, clean, commit, deploy, finalize, open_bounty, review, review_all,
    run_to_review, span_of, NOW_TS, at,
)


def _sheet(contract, publisher):
    return contract.get_errata(addr(publisher), BOUNTY_ID)


def test_the_sheet_lists_only_accepted_defects_in_commit_order(direct_vm, direct_deploy, actors):
    meeting = Spec("meeting", actors[1], CONTRA, D_MEETING)
    fee = Spec("fee", actors[2], ARITH, D_FEE, note="four times 25 is 100, not 120")
    copy = Spec("copy", actors[3], ARITH, D_FEE_VARIANT)            # a duplicate of "fee"
    hall = Spec("hall", actors[4], ARITH, OK_HALL, answer="not_upheld")
    liar = Spec("liar", actors[5], ARITH, MISSING)                  # quote is not in the document
    specs = [meeting, fee, copy, hall, liar]
    contract = deploy(direct_vm, direct_deploy)
    run_to_review(contract, direct_vm, actors[0], specs)
    assert _sheet(contract, actors[0]) == {"finalized": False, "entries": []}  # nothing reviewed yet
    review_all(contract, direct_vm, actors[0], specs)

    projected = _sheet(contract, actors[0])
    assert projected["finalized"] is False
    assert [e["index"] for e in projected["entries"]] == [0, 1]
    assert [e["kind_name"] for e in projected["entries"]] == ["contradiction", "arithmetic"]
    assert [e["reward_wei"] for e in projected["entries"]] == [5 * 10**14, 4 * 10**14]
    assert [e["finder"].lower() for e in projected["entries"]] == [addr(actors[1]), addr(actors[2])]
    assert projected["entries"][1]["note"] == "four times 25 is 100, not 120"
    first = projected["entries"][0]
    assert first["quote_a"] == clean(D_MEETING[0]) and first["quote_b"] == clean(D_MEETING[1])
    assert [first["a_start"], first["a_end"]] == span_of(D_MEETING[0])
    assert [first["b_start"], first["b_end"]] == span_of(D_MEETING[1])

    finalize(contract, direct_vm, actors[9], actors[0])
    settled = _sheet(contract, actors[0])
    assert settled["finalized"] is True and settled["entries"] == projected["entries"]


def test_the_sheet_follows_reviews_as_they_land(direct_vm, direct_deploy, actors):
    fee = Spec("fee", actors[1], ARITH, D_FEE)
    meeting = Spec("meeting", actors[2], CONTRA, D_MEETING)
    contract = deploy(direct_vm, direct_deploy)
    run_to_review(contract, direct_vm, actors[0], [fee, meeting])
    review(contract, direct_vm, actors[0], meeting)
    assert [e["index"] for e in _sheet(contract, actors[0])["entries"]] == [1]
    review(contract, direct_vm, actors[0], fee)
    assert [e["index"] for e in _sheet(contract, actors[0])["entries"]] == [0, 1]


def test_an_upheld_defect_that_finds_the_budget_empty_is_still_listed_with_no_reward(
    direct_vm, direct_deploy, actors
):
    meeting = Spec("meeting", actors[1], CONTRA, D_MEETING)      # 5e14
    fee = Spec("fee", actors[2], ARITH, D_FEE)                    # 4e14
    member = Spec("member", actors[3], DEFN, D_MEMBER)            # 3e14, only 1e14 left
    late = Spec("late", actors[4], ARITH, D_LATE)                 # nothing left
    specs = [meeting, fee, member, late]
    contract = deploy(direct_vm, direct_deploy)
    run_to_review(contract, direct_vm, actors[0], specs, budget=MIN_BUDGET)
    review_all(contract, direct_vm, actors[0], specs)
    finalize(contract, direct_vm, actors[5], actors[0])
    sheet = _sheet(contract, actors[0])
    assert [e["reward_wei"] for e in sheet["entries"]] == [5 * 10**14, 4 * 10**14, 10**14, 0]
    assert [e["index"] for e in sheet["entries"]] == [0, 1, 2, 3]


def test_a_cancelled_bounty_has_an_empty_sheet_and_no_standing(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    open_bounty(contract, direct_vm, actors[0])
    direct_vm.sender = actors[0]
    contract.cancel_bounty(BOUNTY_ID)
    assert _sheet(contract, actors[0]) == {"finalized": False, "entries": []}
    with direct_vm.expect_revert("Bounty is not open"):
        contract.get_standing(addr(actors[0]), BOUNTY_ID)
