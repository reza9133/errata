"""One account may file several findings; each is sealed, revealed, reviewed and settled on its own."""

from tests.direct.support import (
    ARITH, BOND, BUDGET, BOUNTY_ID, COMMIT_BY, CONTRA, D_FEE, D_FEE_VARIANT, D_LATE, D_MEETING,
    D_MEMBER, DEFN, NOW_TS, REVEAL_BY, REWARDS, Spec, addr, at, check_books, commit, credit, deploy,
    finalize, finding_of, open_bounty, reveal, review, review_all, run_to_review, serve, slot_of,
)


def _open(direct_vm, direct_deploy, actors, **kwargs):
    contract = deploy(direct_vm, direct_deploy)
    open_bounty(contract, direct_vm, actors[0], **kwargs)
    direct_vm.warp(at(NOW_TS + 60))
    return contract


def _four(finder):
    return [Spec("one", finder, ARITH, D_FEE), Spec("two", finder, CONTRA, D_MEETING),
            Spec("three", finder, DEFN, D_MEMBER), Spec("four", finder, ARITH, D_LATE)]


def test_one_account_seals_three_findings_but_not_a_fourth(direct_vm, direct_deploy, actors):
    contract = _open(direct_vm, direct_deploy, actors)
    specs = _four(actors[1])
    assert [commit(contract, direct_vm, actors[0], spec) for spec in specs[:3]] == [0, 1, 2]
    with direct_vm.expect_revert("Finding limit per account reached"):
        commit(contract, direct_vm, actors[0], specs[3])
    # Nobody else is affected: another account takes the next index.
    other = Spec("other", actors[2], ARITH, D_FEE_VARIANT)
    assert commit(contract, direct_vm, actors[0], other) == 3
    assert contract.get_accounting()["total_locked_bonds"] == 4 * BOND
    assert [row["index"] for row in contract.get_findings(addr(actors[0]), BOUNTY_ID)["findings"]] == [0, 1, 2, 3]


def test_a_withdrawn_finding_still_counts_toward_the_account_cap(direct_vm, direct_deploy, actors):
    contract = _open(direct_vm, direct_deploy, actors)
    specs = _four(actors[1])
    for spec in specs[:3]:
        commit(contract, direct_vm, actors[0], spec)
    direct_vm.sender = actors[1]
    contract.withdraw_finding(addr(actors[0]), BOUNTY_ID, slot_of(specs[1], actors[0]))
    with direct_vm.expect_revert("Finding limit per account reached"):
        commit(contract, direct_vm, actors[0], specs[3])


def test_the_same_sealed_commitment_cannot_be_filed_twice_by_one_account(direct_vm, direct_deploy, actors):
    contract = _open(direct_vm, direct_deploy, actors)
    spec = Spec("one", actors[1], ARITH, D_FEE)
    commit(contract, direct_vm, actors[0], spec)
    with direct_vm.expect_revert("Commitment already sealed"):
        commit(contract, direct_vm, actors[0], spec)
    assert contract.get_bounty(addr(actors[0]), BOUNTY_ID)["finding_count"] == 1


def test_one_account_files_distinct_and_repeated_defects_and_each_settles_alone(
    direct_vm, direct_deploy, actors
):
    finder = actors[1]
    first = Spec("first", finder, ARITH, D_FEE)
    second = Spec("second", finder, CONTRA, D_MEETING)
    repeat = Spec("repeat", finder, ARITH, D_FEE_VARIANT)   # the same defect as "first"
    specs = [first, second, repeat]
    contract = deploy(direct_vm, direct_deploy)
    run_to_review(contract, direct_vm, actors[0], specs)
    assert [slot_of(spec, actors[0]) for spec in specs] == [0, 1, 2]
    review_all(contract, direct_vm, actors[0], specs)
    finalize(contract, direct_vm, actors[5], actors[0])
    outcomes = [finding_of(contract, actors[0], spec)["outcome"] for spec in specs]
    assert outcomes == ["rewarded", "rewarded", "duplicate_bond_refunded"]
    assert finding_of(contract, actors[0], repeat)["duplicate_of"] == 1
    assert credit(contract, finder) == 3 * BOND + REWARDS[0] + REWARDS[1]
    assert credit(contract, actors[0]) == BUDGET - REWARDS[0] - REWARDS[1]
    check_books(contract, actors[:10])


def test_priority_is_the_commit_index_even_when_the_later_finding_reveals_first(
    direct_vm, direct_deploy, actors
):
    contract = _open(direct_vm, direct_deploy, actors)
    first = Spec("first", actors[1], ARITH, D_FEE_VARIANT)
    second = Spec("second", actors[1], ARITH, D_FEE)
    commit(contract, direct_vm, actors[0], first)
    commit(contract, direct_vm, actors[0], second)
    direct_vm.warp(at(COMMIT_BY))
    reveal(contract, direct_vm, actors[0], second)
    reveal(contract, direct_vm, actors[0], first)
    serve(direct_vm, [first, second])
    direct_vm.warp(at(REVEAL_BY))
    review_all(contract, direct_vm, actors[0], [second, first])
    finalize(contract, direct_vm, actors[5], actors[0])
    assert finding_of(contract, actors[0], first)["outcome"] == "rewarded"
    assert finding_of(contract, actors[0], second)["outcome"] == "duplicate_bond_refunded"
    assert finding_of(contract, actors[0], second)["duplicate_of"] == 1


def test_nobody_else_can_reveal_or_withdraw_someone_elses_finding(direct_vm, direct_deploy, actors):
    contract = _open(direct_vm, direct_deploy, actors)
    spec = Spec("mine", actors[1], ARITH, D_FEE)
    commit(contract, direct_vm, actors[0], spec)
    direct_vm.sender = actors[2]
    with direct_vm.expect_revert("No sealed finding to withdraw"):
        contract.withdraw_finding(addr(actors[0]), BOUNTY_ID, 0)
    direct_vm.warp(at(COMMIT_BY))
    with direct_vm.expect_revert("No sealed finding to reveal"):
        contract.reveal_finding(addr(actors[0]), BOUNTY_ID, 0, ARITH, D_FEE[0], D_FEE[1], "", "", spec.salt)
    direct_vm.sender = actors[1]
    with direct_vm.expect_revert("No sealed finding to reveal"):  # an index that does not exist
        contract.reveal_finding(addr(actors[0]), BOUNTY_ID, 7, ARITH, D_FEE[0], D_FEE[1], "", "", spec.salt)
    reveal(contract, direct_vm, actors[0], spec)  # the owner still can
    assert finding_of(contract, actors[0], spec)["status"] == "revealed"


def test_each_finding_is_reviewed_by_index_by_its_finder_or_the_publisher_only(
    direct_vm, direct_deploy, actors
):
    mine = Spec("mine", actors[1], ARITH, D_FEE)
    also_mine = Spec("also", actors[1], CONTRA, D_MEETING)
    contract = deploy(direct_vm, direct_deploy)
    run_to_review(contract, direct_vm, actors[0], [mine, also_mine])
    with direct_vm.expect_revert("Only the publisher or the finder"):
        review(contract, direct_vm, actors[0], also_mine, caller=actors[5])
    review(contract, direct_vm, actors[0], also_mine, caller=actors[1])      # the finder
    review(contract, direct_vm, actors[0], mine, caller=actors[0])           # the publisher
    assert finding_of(contract, actors[0], mine)["status"] == "upheld"
    assert finding_of(contract, actors[0], also_mine)["status"] == "upheld"
