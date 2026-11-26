"""Whole lifecycles: sealing, reveal, review, finalization and withdrawal."""


from tests.direct.support import (
    slot_of,
    ARITH, BOND, BUDGET, COMMIT_BY, CONTRA, D_FEE, D_FEE_VARIANT, D_MEETING, D_MEMBER, DEFN,
    MISSING, NOW_TS, OK_HALL, REVEAL_BY, REVIEW_BY, REWARDS, Spec, addr, at, bounty_of,
    check_books, commit, credit, deploy, finalize, finding_of, open_bounty, reveal, review,
    review_all, run_to_review, serve, span_of,
)


def test_a_full_bounty_pays_distinct_defects_and_refunds_the_rest(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    publisher = actors[0]
    first = Spec("first", actors[1], ARITH, D_FEE)
    second = Spec("second", actors[2], CONTRA, D_MEETING)
    decoy = Spec("decoy", actors[3], ARITH, OK_HALL, answer="not_upheld")
    copycat = Spec("copycat", actors[4], ARITH, D_FEE_VARIANT)
    liar = Spec("liar", actors[5], ARITH, MISSING)
    sleeper = Spec("sleeper", actors[6], DEFN, D_MEMBER)
    specs = [first, second, decoy, copycat, liar, sleeper]

    open_bounty(contract, direct_vm, publisher)
    direct_vm.warp(at(NOW_TS + 60))
    for spec in specs:
        commit(contract, direct_vm, publisher, spec)
    assert bounty_of(contract, publisher)["finding_count"] == 6
    direct_vm.warp(at(COMMIT_BY))
    for spec in specs[:-1]:  # the sleeper never reveals
        reveal(contract, direct_vm, publisher, spec)
    serve(direct_vm, specs)
    direct_vm.warp(at(REVEAL_BY))
    review_all(contract, direct_vm, publisher, specs[:-1])

    assert finding_of(contract, publisher, first)["status"] == "upheld"
    assert finding_of(contract, publisher, second)["status"] == "upheld"
    assert finding_of(contract, publisher, decoy)["status"] == "not_upheld"
    assert finding_of(contract, publisher, copycat)["status"] == "upheld"
    liar_state = finding_of(contract, publisher, liar)
    assert liar_state["status"] == "disqualified"
    assert liar_state["reason"] == "evidence:quote_a_not_found"
    assert finding_of(contract, publisher, first)["a_start"] == span_of(D_FEE[0])[0]

    finalize(contract, direct_vm, actors[9], publisher)
    bounty = bounty_of(contract, publisher)
    assert bounty["status"] == "finalized"
    assert bounty["settlement"] == "rewards_paid_for_distinct_upheld_findings"
    assert bounty["rewarded_count"] == 2
    assert bounty["paid_wei"] == REWARDS[0] + REWARDS[1]
    assert bounty["forfeited_wei"] == 2 * BOND  # liar + sleeper
    assert bounty["refunded_wei"] == 4 * BOND   # first, second, decoy, copycat
    assert bounty["returned_wei"] == BUDGET - REWARDS[0] - REWARDS[1]

    assert credit(contract, first.who) == BOND + REWARDS[0]
    assert credit(contract, second.who) == BOND + REWARDS[1]
    assert credit(contract, decoy.who) == BOND
    assert credit(contract, copycat.who) == BOND
    assert credit(contract, liar.who) == 0
    assert credit(contract, sleeper.who) == 0
    assert credit(contract, publisher) == BUDGET - REWARDS[0] - REWARDS[1] + 2 * BOND
    copy_state = finding_of(contract, publisher, copycat)
    assert copy_state["outcome"] == "duplicate_bond_refunded"
    assert copy_state["duplicate_of"] == 1  # index 0 + 1
    assert finding_of(contract, publisher, first)["reward_wei"] == REWARDS[0]
    accounting = check_books(contract, actors[:10], [(publisher, "B-001")])
    assert accounting["total_locked_budgets"] == 0 and accounting["total_locked_bonds"] == 0

    for spec in (first, second, decoy, copycat):
        direct_vm.sender = spec.who
        contract.withdraw_credit()
        assert credit(contract, spec.who) == 0
    direct_vm.sender = publisher
    contract.withdraw_credit()
    assert check_books(contract, actors[:10], [(publisher, "B-001")])["accounted_liabilities"] == 0


def test_cancel_before_any_finding_returns_the_budget(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    open_bounty(contract, direct_vm, actors[0])
    assert credit(contract, actors[0]) == 0
    direct_vm.sender = actors[0]
    contract.cancel_bounty("B-001")
    assert credit(contract, actors[0]) == BUDGET
    bounty = bounty_of(contract, actors[0])
    assert bounty["status"] == "cancelled" and bounty["returned_wei"] == BUDGET
    check_books(contract, actors[:10])


def test_a_bounty_with_a_sealed_finding_cannot_be_cancelled(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    open_bounty(contract, direct_vm, actors[0])
    direct_vm.warp(at(NOW_TS + 60))
    commit(contract, direct_vm, actors[0], Spec("a", actors[1], ARITH, D_FEE))
    direct_vm.sender = actors[0]
    with direct_vm.expect_revert():
        contract.cancel_bounty("B-001")
    direct_vm.sender = actors[2]
    with direct_vm.expect_revert():
        contract.cancel_bounty("B-001")


def test_no_upheld_finding_returns_the_whole_budget(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    decoy = Spec("decoy", actors[1], ARITH, OK_HALL, answer="not_upheld")
    run_to_review(contract, direct_vm, actors[0], [decoy])
    review(contract, direct_vm, actors[0], decoy)
    finalize(contract, direct_vm, actors[2], actors[0])
    bounty = bounty_of(contract, actors[0])
    assert bounty["settlement"] == "no_reward_budget_returned"
    assert credit(contract, actors[0]) == BUDGET
    assert credit(contract, decoy.who) == BOND
    check_books(contract, actors[:10])


def test_an_empty_bounty_can_be_finalized_after_the_reveal_window(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    open_bounty(contract, direct_vm, actors[0])
    direct_vm.warp(at(REVEAL_BY))
    finalize(contract, direct_vm, actors[3], actors[0])
    assert credit(contract, actors[0]) == BUDGET
    assert bounty_of(contract, actors[0])["status"] == "finalized"


def test_withdrawing_before_the_deadline_refunds_the_bond_but_keeps_the_slot(
    direct_vm, direct_deploy, actors
):
    contract = deploy(direct_vm, direct_deploy)
    open_bounty(contract, direct_vm, actors[0], max_findings=2)
    direct_vm.warp(at(NOW_TS + 60))
    quitter = Spec("quitter", actors[1], ARITH, D_FEE)
    commit(contract, direct_vm, actors[0], quitter)
    direct_vm.sender = quitter.who
    contract.withdraw_finding(addr(actors[0]), "B-001", slot_of(quitter, actors[0]))
    assert credit(contract, quitter.who) == BOND
    assert finding_of(contract, actors[0], quitter)["status"] == "withdrawn"
    # The slot stays used. The very same sealed commitment cannot be filed twice, but the quitter
    # may still seal a different finding, which takes the next slot.
    with direct_vm.expect_revert("Commitment already sealed"):
        commit(contract, direct_vm, actors[0], quitter)
    commit(contract, direct_vm, actors[0], Spec("again", actors[1], ARITH, D_FEE_VARIANT))
    with direct_vm.expect_revert("Bounty is full"):
        commit(contract, direct_vm, actors[0], Spec("c", actors[3], ARITH, D_FEE))
    direct_vm.warp(at(REVEAL_BY))
    finalize(contract, direct_vm, actors[5], actors[0])
    check_books(contract, actors[:10])


def test_finalization_waits_for_review_until_the_review_window_ends(
    direct_vm, direct_deploy, actors
):
    contract = deploy(direct_vm, direct_deploy)
    spec = Spec("slow", actors[1], ARITH, D_FEE)
    run_to_review(contract, direct_vm, actors[0], [spec])
    with direct_vm.expect_revert():
        finalize(contract, direct_vm, actors[2], actors[0])
    direct_vm.warp(at(REVIEW_BY))
    finalize(contract, direct_vm, actors[2], actors[0])
    state = finding_of(contract, actors[0], spec)
    assert state["outcome"] == "unresolved_bond_refunded"
    assert credit(contract, spec.who) == BOND
    assert credit(contract, actors[0]) == BUDGET
    check_books(contract, actors[:10])


def test_finalizing_twice_or_too_early_fails(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    open_bounty(contract, direct_vm, actors[0])
    with direct_vm.expect_revert():
        finalize(contract, direct_vm, actors[1], actors[0])
    direct_vm.warp(at(REVEAL_BY))
    finalize(contract, direct_vm, actors[1], actors[0])
    with direct_vm.expect_revert():
        finalize(contract, direct_vm, actors[1], actors[0])
