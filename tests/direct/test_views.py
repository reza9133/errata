"""Read-only surface: info, listings and error behaviour for unknown objects."""


from tests.direct.support import (
    ARITH, BOND, BUDGET, CONTRA, D_FEE, D_MEETING, REWARDS, Spec, addr, bounty_of, commit, deploy,
    finding_of, open_bounty, at, NOW_TS,
)


def test_info_describes_the_rules_the_contract_enforces(direct_vm, direct_deploy, actors):
    info = deploy(direct_vm, direct_deploy).get_info()
    assert info["name"] == "Errata" and info["version"] == "0.2.0"
    assert [k["name"] for k in info["kinds"]] == ["arithmetic", "contradiction", "definition_conflict"]
    assert [k["kind"] for k in info["kinds"]] == [1, 2, 3]
    assert info["context_radii"] == [300, 900, 2000]
    assert info["max_assessments_per_finding"] == 3
    assert info["max_quote_chars"] == 120 and info["min_quote_chars"] == 8
    assert info["max_findings_per_account"] == 3
    assert "pull-payment" in info["settlement_model"]
    assert "forfeited to the publisher only" in info["forfeiture_policy"]


def test_findings_are_listed_in_commitment_order(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    open_bounty(contract, direct_vm, actors[0])
    direct_vm.warp(at(NOW_TS + 60))
    order = [actors[4], actors[2], actors[3]]
    for index, who in enumerate(order):
        assert commit(contract, direct_vm, actors[0], Spec(f"s{index}", who, ARITH, D_FEE)) == index
    listed = contract.get_findings(addr(actors[0]), "B-001")["findings"]
    assert [item["finder"].lower() for item in listed] == [addr(who) for who in order]
    assert [item["index"] for item in listed] == [0, 1, 2]
    assert contract.get_finding(addr(actors[0]), "B-001", 2)["finder"].lower() == addr(actors[3])


def test_a_sealed_finding_reveals_nothing_about_its_content(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    open_bounty(contract, direct_vm, actors[0])
    direct_vm.warp(at(NOW_TS + 60))
    spec = Spec("sealed", actors[1], CONTRA, D_MEETING, note="secret note")
    commit(contract, direct_vm, actors[0], spec)
    state = finding_of(contract, actors[0], spec)
    assert state["status"] == "committed"
    assert state["kind"] == 0 and state["kind_name"] == ""
    assert state["quote_a"] == "" and state["quote_b"] == "" and state["note"] == ""
    assert state["bond_wei"] == BOND


def test_bounty_view_reports_the_terms_and_deadlines(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    open_bounty(contract, direct_vm, actors[0])
    bounty = bounty_of(contract, actors[0])
    assert (bounty["reward_arithmetic"], bounty["reward_contradiction"], bounty["reward_definition"]) == REWARDS
    assert bounty["budget_wei"] == BUDGET and bounty["bond_wei"] == BOND
    assert bounty["review_by"] == bounty["reveal_by"] + 3 * 86400
    assert bounty["status"] == "open" and bounty["settlement"] == ""


def test_unknown_objects_revert_instead_of_returning_defaults(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    open_bounty(contract, direct_vm, actors[0])
    with direct_vm.expect_revert():
        contract.get_bounty(addr(actors[0]), "NOPE")
    with direct_vm.expect_revert():
        contract.get_finding(addr(actors[0]), "B-001", 7)
    with direct_vm.expect_revert():
        contract.get_findings(addr(actors[0]), "NOPE")
    with direct_vm.expect_revert():
        contract.get_errata(addr(actors[0]), "NOPE")
    with direct_vm.expect_revert():
        contract.get_standing(addr(actors[0]), "NOPE")
    assert contract.get_findings(addr(actors[0]), "B-001") == {"findings": []}
    assert contract.get_errata(addr(actors[0]), "B-001") == {"finalized": False, "entries": []}
