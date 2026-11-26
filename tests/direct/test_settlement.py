"""Settlement: duplicates, priority, budget exhaustion, forfeits and the books."""


from tests.direct.support import (
    slot_of,
    ARITH, BOND, BUDGET, CONTRA, D_FEE, D_FEE_VARIANT, D_LATE, D_MEETING, D_MEMBER, DEFN,
    MISSING, MIN_BUDGET, NOW_TS, OK_HALL, REVEAL_BY, REVIEW_BY, REWARDS, Spec, addr, at,
    bounty_of, check_books, commit, credit, deploy, deposit, finalize, finding_of, open_bounty,
    reveal, review, review_all, run_to_review, serve,
)


def _run(direct_vm, direct_deploy, actors, specs, **kwargs):
    contract = deploy(direct_vm, direct_deploy)
    run_to_review(contract, direct_vm, actors[0], specs, **kwargs)
    return contract


def _outcomes(contract, publisher, specs):
    return [finding_of(contract, publisher, spec)["outcome"] for spec in specs]


# ------------------------------------------------------------------ duplicates


def test_the_same_pair_in_the_other_order_is_a_duplicate(direct_vm, direct_deploy, actors):
    first = Spec("first", actors[1], ARITH, D_FEE)
    flipped = Spec("flipped", actors[2], ARITH, (D_FEE[1], D_FEE[0]))
    contract = _run(direct_vm, direct_deploy, actors, [first, flipped])
    review_all(contract, direct_vm, actors[0], [first, flipped])
    finalize(contract, direct_vm, actors[5], actors[0])
    assert _outcomes(contract, actors[0], [first, flipped]) == ["rewarded", "duplicate_bond_refunded"]
    assert finding_of(contract, actors[0], flipped)["duplicate_of"] == 1


def test_a_shared_anchor_with_a_different_partner_is_a_different_defect(
    direct_vm, direct_deploy, actors
):
    first = Spec("first", actors[1], ARITH, D_FEE)
    other = Spec("other", actors[2], ARITH, (D_FEE[0], D_LATE[1]))
    contract = _run(direct_vm, direct_deploy, actors, [first, other])
    review_all(contract, direct_vm, actors[0], [first, other])
    finalize(contract, direct_vm, actors[5], actors[0])
    assert _outcomes(contract, actors[0], [first, other]) == ["rewarded", "rewarded"]
    assert bounty_of(contract, actors[0])["paid_wei"] == 2 * REWARDS[0]


def test_a_defect_filed_under_another_kind_is_still_a_duplicate(direct_vm, direct_deploy, actors):
    first = Spec("first", actors[1], ARITH, D_FEE)
    relabelled = Spec("relabelled", actors[2], CONTRA, D_FEE)
    contract = _run(direct_vm, direct_deploy, actors, [first, relabelled])
    review_all(contract, direct_vm, actors[0], [first, relabelled])
    finalize(contract, direct_vm, actors[5], actors[0])
    assert _outcomes(contract, actors[0], [first, relabelled]) == ["rewarded", "duplicate_bond_refunded"]
    # The earlier finding's kind sets the prize; relabelling cannot raise it.
    assert finding_of(contract, actors[0], first)["reward_wei"] == REWARDS[0]


def test_priority_follows_commitment_order_not_review_order(direct_vm, direct_deploy, actors):
    early = Spec("early", actors[1], ARITH, D_FEE)
    late = Spec("late", actors[2], ARITH, D_FEE_VARIANT)
    contract = _run(direct_vm, direct_deploy, actors, [early, late])
    review(contract, direct_vm, actors[0], late)   # the later committer is reviewed first
    review(contract, direct_vm, actors[0], early)
    finalize(contract, direct_vm, actors[5], actors[0])
    assert _outcomes(contract, actors[0], [early, late]) == ["rewarded", "duplicate_bond_refunded"]


def test_every_later_copy_points_at_the_first_upheld_finding(direct_vm, direct_deploy, actors):
    one = Spec("one", actors[1], ARITH, D_FEE)
    two = Spec("two", actors[2], ARITH, D_FEE_VARIANT)
    three = Spec("three", actors[3], CONTRA, (D_FEE[1], D_FEE_VARIANT[0]))
    contract = _run(direct_vm, direct_deploy, actors, [one, two, three])
    review_all(contract, direct_vm, actors[0], [one, two, three])
    finalize(contract, direct_vm, actors[5], actors[0])
    assert [finding_of(contract, actors[0], s)["duplicate_of"] for s in (one, two, three)] == [0, 1, 1]
    assert bounty_of(contract, actors[0])["rewarded_count"] == 1


def test_a_finding_that_was_not_upheld_does_not_shield_later_ones(direct_vm, direct_deploy, actors):
    first = Spec("first", actors[1], ARITH, D_FEE, answer="not_upheld")
    second = Spec("second", actors[2], ARITH, D_FEE_VARIANT)
    contract = _run(direct_vm, direct_deploy, actors, [first, second])
    review_all(contract, direct_vm, actors[0], [first, second])
    finalize(contract, direct_vm, actors[5], actors[0])
    assert _outcomes(contract, actors[0], [first, second]) == ["not_upheld_bond_refunded", "rewarded"]


def test_disqualified_unrevealed_and_unresolved_findings_do_not_shield_later_ones(
    direct_vm, direct_deploy, actors
):
    contract = deploy(direct_vm, direct_deploy)
    bad = Spec("bad", actors[1], ARITH, (D_FEE[0], "this passage does not exist"))
    ghost = Spec("ghost", actors[2], ARITH, D_FEE)
    stuck = Spec("stuck", actors[3], ARITH, D_FEE, answer="unclear")
    real = Spec("real", actors[4], ARITH, D_FEE_VARIANT)
    open_bounty(contract, direct_vm, actors[0])
    direct_vm.warp(at(NOW_TS + 60))
    for spec in (bad, ghost, stuck, real):
        commit(contract, direct_vm, actors[0], spec)
    direct_vm.warp(at(NOW_TS + 3600))
    for spec in (bad, stuck, real):  # ghost never reveals
        reveal(contract, direct_vm, actors[0], spec)
    serve(direct_vm, [bad, stuck, real])
    direct_vm.warp(at(REVEAL_BY))
    review(contract, direct_vm, actors[0], bad)
    review(contract, direct_vm, actors[0], real)
    clock = REVEAL_BY
    for _ in range(3):
        direct_vm.warp(at(clock))
        review(contract, direct_vm, actors[0], stuck)
        clock = finding_of(contract, actors[0], stuck)["retry_at"]
    finalize(contract, direct_vm, actors[5], actors[0])
    assert _outcomes(contract, actors[0], [bad, ghost, stuck, real]) == [
        "disqualified_bond_forfeited", "unrevealed_bond_forfeited", "unresolved_bond_refunded",
        "rewarded"]
    assert credit(contract, actors[0]) == BUDGET - REWARDS[0] + 2 * BOND
    check_books(contract, actors[:10])


# --------------------------------------------------------------- budget limits


def test_the_last_reward_is_partial_and_later_findings_get_nothing(direct_vm, direct_deploy, actors):
    meeting = Spec("meeting", actors[1], CONTRA, D_MEETING)      # 5e14
    fee = Spec("fee", actors[2], ARITH, D_FEE)                    # 4e14
    member = Spec("member", actors[3], DEFN, D_MEMBER)            # 3e14, only 1e14 left
    late = Spec("late", actors[4], ARITH, D_LATE)                 # nothing left
    specs = [meeting, fee, member, late]
    contract = _run(direct_vm, direct_deploy, actors, specs, budget=MIN_BUDGET)
    review_all(contract, direct_vm, actors[0], specs)
    finalize(contract, direct_vm, actors[5], actors[0])
    assert _outcomes(contract, actors[0], specs) == [
        "rewarded", "rewarded", "rewarded", "upheld_budget_exhausted_bond_refunded"]
    rewards = [finding_of(contract, actors[0], s)["reward_wei"] for s in specs]
    assert rewards == [5 * 10**14, 4 * 10**14, 10**14, 0]
    bounty = bounty_of(contract, actors[0])
    assert bounty["paid_wei"] == MIN_BUDGET and bounty["returned_wei"] == 0
    assert credit(contract, late.who) == BOND  # the bond is still refunded in full
    assert credit(contract, member.who) == BOND + 10**14
    assert credit(contract, actors[0]) == 0
    check_books(contract, actors[:10])


def test_a_budget_that_exactly_covers_every_reward_leaves_nothing_over(
    direct_vm, direct_deploy, actors
):
    specs = [Spec("a", actors[1], ARITH, D_FEE), Spec("b", actors[2], CONTRA, D_MEETING),
             Spec("c", actors[3], DEFN, D_MEMBER)]
    contract = _run(direct_vm, direct_deploy, actors, specs, budget=sum(REWARDS))
    review_all(contract, direct_vm, actors[0], specs)
    finalize(contract, direct_vm, actors[5], actors[0])
    assert bounty_of(contract, actors[0])["returned_wei"] == 0
    assert bounty_of(contract, actors[0])["paid_wei"] == sum(REWARDS)
    check_books(contract, actors[:10])


def test_rewards_are_paid_in_commitment_order_when_money_is_short(direct_vm, direct_deploy, actors):
    # Only one full reward fits. The earlier committer takes it, whatever the review order.
    first = Spec("first", actors[1], CONTRA, D_MEETING)
    second = Spec("second", actors[2], CONTRA, (D_MEETING[0], D_LATE[1]))
    contract = _run(direct_vm, direct_deploy, actors, [first, second], rewards=(0, 5 * 10**14, 0),
                    budget=MIN_BUDGET, bond=10**14)
    review(contract, direct_vm, actors[0], second)
    review(contract, direct_vm, actors[0], first)
    finalize(contract, direct_vm, actors[5], actors[0])
    assert finding_of(contract, actors[0], first)["reward_wei"] == 5 * 10**14
    assert finding_of(contract, actors[0], second)["reward_wei"] == 5 * 10**14
    assert bounty_of(contract, actors[0])["paid_wei"] == MIN_BUDGET


# -------------------------------------------------------------- forfeits & books


def test_forfeited_bonds_go_to_the_publisher_and_nobody_else(direct_vm, direct_deploy, actors):
    bad = Spec("bad", actors[1], ARITH, MISSING)
    ghost = Spec("ghost", actors[2], ARITH, D_FEE)
    contract = deploy(direct_vm, direct_deploy)
    open_bounty(contract, direct_vm, actors[0])
    direct_vm.warp(at(NOW_TS + 60))
    commit(contract, direct_vm, actors[0], bad)
    commit(contract, direct_vm, actors[0], ghost)
    direct_vm.warp(at(NOW_TS + 3600))
    reveal(contract, direct_vm, actors[0], bad)
    serve(direct_vm, [bad, ghost])
    direct_vm.warp(at(REVEAL_BY))
    review(contract, direct_vm, actors[0], bad)
    finalize(contract, direct_vm, actors[7], actors[0])
    bounty = bounty_of(contract, actors[0])
    assert bounty["forfeited_wei"] == 2 * BOND and bounty["refunded_wei"] == 0
    assert credit(contract, actors[0]) == BUDGET + 2 * BOND
    assert credit(contract, actors[7]) == 0  # the finalizer earns nothing
    assert credit(contract, bad.who) == 0 and credit(contract, ghost.who) == 0
    assert finding_of(contract, actors[0], bad)["bond_wei"] == 0
    check_books(contract, actors[:10])


def test_the_standing_preview_matches_what_finalization_does(direct_vm, direct_deploy, actors):
    specs = [
        Spec("a", actors[1], ARITH, D_FEE),
        Spec("b", actors[2], ARITH, D_FEE_VARIANT),
        Spec("c", actors[3], CONTRA, D_MEETING),
        Spec("d", actors[4], ARITH, OK_HALL, answer="not_upheld"),
        Spec("e", actors[5], ARITH, MISSING),
        Spec("f", actors[6], DEFN, D_MEMBER, answer="unclear"),
    ]
    contract = _run(direct_vm, direct_deploy, actors, specs)
    review_all(contract, direct_vm, actors[0], specs)
    preview = contract.get_standing(addr(actors[0]), "B-001")
    direct_vm.warp(at(REVIEW_BY))  # "f" stays unclear, so wait for the review window to end
    finalize(contract, direct_vm, actors[9], actors[0])
    assert [e["projected_outcome"] for e in preview["entries"]] == _outcomes(contract, actors[0], specs)
    assert [e["projected_reward"] for e in preview["entries"]] == [
        finding_of(contract, actors[0], s)["reward_wei"] for s in specs]
    assert [e["duplicate_of"] for e in preview["entries"]] == [
        finding_of(contract, actors[0], s)["duplicate_of"] for s in specs]
    bounty = bounty_of(contract, actors[0])
    assert preview["projected_paid"] == bounty["paid_wei"]
    assert preview["projected_returned"] == bounty["returned_wei"]
    assert preview["projected_forfeited"] == bounty["forfeited_wei"]
    # After finalization the same call reports the settled result instead of a projection.
    settled = contract.get_standing(addr(actors[0]), "B-001")
    assert settled["finalized"] is True and preview["finalized"] is False
    assert settled["entries"] == preview["entries"]
    assert settled["projected_paid"] == bounty["paid_wei"]


def test_the_preview_is_read_only_and_tracks_reviews_as_they_land(direct_vm, direct_deploy, actors):
    spec = Spec("a", actors[1], ARITH, D_FEE)
    contract = _run(direct_vm, direct_deploy, actors, [spec])
    before = contract.get_accounting()
    entry = contract.get_standing(addr(actors[0]), "B-001")["entries"][0]
    assert entry["projected_outcome"] == "unresolved_bond_refunded" and entry["projected_reward"] == 0
    review(contract, direct_vm, actors[0], spec)
    entry = contract.get_standing(addr(actors[0]), "B-001")["entries"][0]
    assert entry["projected_outcome"] == "rewarded" and entry["projected_reward"] == REWARDS[0]
    assert contract.get_accounting() == before
    assert finding_of(contract, actors[0], spec)["outcome"] == ""  # nothing is written until finalize


def test_all_findings_withdrawn_returns_everything(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    specs = [Spec("a", actors[1], ARITH, D_FEE), Spec("b", actors[2], CONTRA, D_MEETING)]
    open_bounty(contract, direct_vm, actors[0])
    direct_vm.warp(at(NOW_TS + 60))
    for spec in specs:
        commit(contract, direct_vm, actors[0], spec)
        direct_vm.sender = spec.who
        contract.withdraw_finding(addr(actors[0]), "B-001", slot_of(spec, actors[0]))
    direct_vm.warp(at(REVEAL_BY))
    finalize(contract, direct_vm, actors[5], actors[0])
    assert credit(contract, actors[0]) == BUDGET
    for spec in specs:
        assert credit(contract, spec.who) == BOND
        assert finding_of(contract, actors[0], spec)["outcome"] == "withdrawn_bond_refunded"
    check_books(contract, actors[:10])


def test_two_bounties_never_touch_each_others_escrow(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    one = Spec("one", actors[2], ARITH, D_FEE)
    two = Spec("two", actors[2], CONTRA, D_MEETING)  # same finder, different bounties
    open_bounty(contract, direct_vm, actors[0], "A")
    open_bounty(contract, direct_vm, actors[1], "A")
    direct_vm.warp(at(NOW_TS + 60))
    deposit(contract, direct_vm, actors[2], 2 * BOND)
    direct_vm.sender = actors[2]
    one.slots[(addr(actors[0]), "A")] = contract.commit_finding(addr(actors[0]), "A", one.seal(actors[0], "A"))
    two.slots[(addr(actors[1]), "A")] = contract.commit_finding(addr(actors[1]), "A", two.seal(actors[1], "A"))
    direct_vm.warp(at(NOW_TS + 3600))
    reveal(contract, direct_vm, actors[0], one, "A")
    reveal(contract, direct_vm, actors[1], two, "A")
    serve(direct_vm, [one, two])
    direct_vm.warp(at(REVEAL_BY))
    review(contract, direct_vm, actors[0], one, bounty_id="A")
    finalize(contract, direct_vm, actors[5], actors[0], "A")
    assert bounty_of(contract, actors[0], "A")["status"] == "finalized"
    assert bounty_of(contract, actors[1], "A")["status"] == "open"
    accounting = check_books(contract, actors[:10], [(actors[0], "A"), (actors[1], "A")])
    assert accounting["total_locked_budgets"] == BUDGET
    assert accounting["total_locked_bonds"] == BOND  # only bounty two still holds a bond
    assert credit(contract, actors[2]) == BOND + REWARDS[0]


def test_finalizing_leaves_later_commits_and_reveals_impossible(direct_vm, direct_deploy, actors):
    spec = Spec("a", actors[1], ARITH, D_FEE)
    contract = _run(direct_vm, direct_deploy, actors, [spec])
    review(contract, direct_vm, actors[0], spec)
    finalize(contract, direct_vm, actors[5], actors[0])
    latecomer = Spec("late", actors[2], ARITH, D_FEE_VARIANT)
    with direct_vm.expect_revert():
        commit(contract, direct_vm, actors[0], latecomer)
    with direct_vm.expect_revert():
        reveal(contract, direct_vm, actors[0], spec)
    direct_vm.sender = spec.who
    with direct_vm.expect_revert():
        contract.withdraw_finding(addr(actors[0]), "B-001", slot_of(spec, actors[0]))
    with direct_vm.expect_revert():
        direct_vm.sender = actors[0]
        contract.cancel_bounty("B-001")


def test_finalize_does_not_pay_native_gen_only_withdrawal_does(direct_vm, direct_deploy, actors):
    spec = Spec("a", actors[1], ARITH, D_FEE)
    contract = _run(direct_vm, direct_deploy, actors, [spec])
    review(contract, direct_vm, actors[0], spec)
    finalize(contract, direct_vm, actors[5], actors[0])
    assert credit(contract, spec.who) == BOND + REWARDS[0]
    direct_vm.sender = spec.who
    contract.withdraw_credit()
    assert credit(contract, spec.who) == 0
    assert contract.get_accounting()["total_credits"] == credit(contract, actors[0])
