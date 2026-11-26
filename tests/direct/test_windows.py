"""Exact deadline boundaries, and spans that touch without overlapping."""


from tests.direct.support import (
    slot_of,
    ARITH, BOND, COMMIT_BY, CONTRA, D_FEE, D_MEETING, NOW_TS, REVEAL_BY, REWARDS, SUBJECT_TEXT,
    Spec, addr, at, bounty_of, commit, credit, deploy, finalize, finding_of, open_bounty, reveal,
    review, review_all, run_to_review,
)


def _sealed_pair(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    open_bounty(contract, direct_vm, actors[0])
    direct_vm.warp(at(NOW_TS + 60))
    early = Spec("early", actors[1], ARITH, D_FEE)
    other = Spec("other", actors[2], CONTRA, D_MEETING)
    commit(contract, direct_vm, actors[0], early)
    commit(contract, direct_vm, actors[0], other)
    return contract, early, other


def test_reveal_opens_exactly_at_the_sealing_deadline(direct_vm, direct_deploy, actors):
    contract, early, other = _sealed_pair(direct_vm, direct_deploy, actors)
    direct_vm.warp(at(COMMIT_BY - 1))
    with direct_vm.expect_revert():
        reveal(contract, direct_vm, actors[0], early)
    direct_vm.warp(at(COMMIT_BY))
    reveal(contract, direct_vm, actors[0], early)
    assert finding_of(contract, actors[0], early)["status"] == "revealed"


def test_reveal_closes_exactly_at_the_reveal_deadline(direct_vm, direct_deploy, actors):
    contract, early, other = _sealed_pair(direct_vm, direct_deploy, actors)
    direct_vm.warp(at(REVEAL_BY - 1))
    reveal(contract, direct_vm, actors[0], early)
    direct_vm.warp(at(REVEAL_BY))
    with direct_vm.expect_revert():
        reveal(contract, direct_vm, actors[0], other)
    assert finding_of(contract, actors[0], other)["status"] == "committed"


def test_a_finding_cannot_be_withdrawn_once_sealing_has_closed(direct_vm, direct_deploy, actors):
    """Otherwise a finder could dodge the forfeiture for not revealing."""
    contract, early, other = _sealed_pair(direct_vm, direct_deploy, actors)
    direct_vm.warp(at(COMMIT_BY - 1))
    direct_vm.sender = early.who
    contract.withdraw_finding(addr(actors[0]), "B-001", slot_of(early, actors[0]))  # last second is fine
    assert credit(contract, early.who) == BOND
    for moment in (COMMIT_BY, COMMIT_BY + 1, REVEAL_BY - 1, REVEAL_BY):
        direct_vm.warp(at(moment))
        direct_vm.sender = other.who
        with direct_vm.expect_revert():
            contract.withdraw_finding(addr(actors[0]), "B-001", slot_of(other, actors[0]))
    assert credit(contract, other.who) == 0
    finalize(contract, direct_vm, actors[5], actors[0])
    assert finding_of(contract, actors[0], other)["outcome"] == "unrevealed_bond_forfeited"


def test_a_revealed_finding_cannot_be_withdrawn(direct_vm, direct_deploy, actors):
    contract, early, other = _sealed_pair(direct_vm, direct_deploy, actors)
    direct_vm.warp(at(COMMIT_BY))
    reveal(contract, direct_vm, actors[0], early)
    direct_vm.sender = early.who
    with direct_vm.expect_revert():
        contract.withdraw_finding(addr(actors[0]), "B-001", slot_of(early, actors[0]))


def test_a_withdrawn_finding_cannot_be_withdrawn_twice_or_revealed(direct_vm, direct_deploy, actors):
    contract, early, other = _sealed_pair(direct_vm, direct_deploy, actors)
    direct_vm.sender = early.who
    contract.withdraw_finding(addr(actors[0]), "B-001", slot_of(early, actors[0]))
    with direct_vm.expect_revert():
        contract.withdraw_finding(addr(actors[0]), "B-001", slot_of(early, actors[0]))
    direct_vm.warp(at(COMMIT_BY))
    with direct_vm.expect_revert():
        reveal(contract, direct_vm, actors[0], early)
    assert credit(contract, early.who) == BOND


# ------------------------------------------------------ touching is not overlapping

# "The annual fee is 120 dollars" ends exactly where ", payable in 4 equal ..." begins.
LEFT = "The annual fee is 120 dollars"
RIGHT = ", payable in 4 equal quarterly instalments of 25 dollars each"
PARTNER = "Minutes are published within 7 days of each assembly meeting"


def test_the_fixture_really_has_adjacent_passages():
    assert SUBJECT_TEXT.count(LEFT) == 1 and SUBJECT_TEXT.count(RIGHT) == 1
    assert SUBJECT_TEXT.find(LEFT) + len(LEFT) == SUBJECT_TEXT.find(RIGHT)


def test_two_quotes_that_touch_are_not_overlapping(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    spec = Spec("touching", actors[1], ARITH, (LEFT, RIGHT))
    run_to_review(contract, direct_vm, actors[0], [spec])
    review(contract, direct_vm, actors[0], spec)
    state = finding_of(contract, actors[0], spec)
    assert state["status"] == "upheld", state["reason"]
    assert state["a_end"] == state["b_start"]


def test_findings_whose_anchors_only_touch_are_different_defects(direct_vm, direct_deploy, actors):
    left = Spec("left", actors[1], ARITH, (LEFT, PARTNER))
    right = Spec("right", actors[2], ARITH, (RIGHT, PARTNER))
    contract = deploy(direct_vm, direct_deploy)
    run_to_review(contract, direct_vm, actors[0], [left, right])
    review_all(contract, direct_vm, actors[0], [left, right])
    finalize(contract, direct_vm, actors[5], actors[0])
    assert finding_of(contract, actors[0], left)["outcome"] == "rewarded"
    assert finding_of(contract, actors[0], right)["outcome"] == "rewarded"
    assert bounty_of(contract, actors[0])["paid_wei"] == 2 * REWARDS[0]


def test_one_character_of_overlap_is_enough_to_be_a_duplicate(direct_vm, direct_deploy, actors):
    overlapping = LEFT + ","  # shares the final comma position with RIGHT's first character
    left = Spec("left", actors[1], ARITH, (LEFT, PARTNER))
    nudged = Spec("nudged", actors[2], ARITH, (overlapping, PARTNER))
    contract = deploy(direct_vm, direct_deploy)
    run_to_review(contract, direct_vm, actors[0], [left, nudged])
    review_all(contract, direct_vm, actors[0], [left, nudged])
    finalize(contract, direct_vm, actors[5], actors[0])
    assert finding_of(contract, actors[0], nudged)["outcome"] == "duplicate_bond_refunded"
