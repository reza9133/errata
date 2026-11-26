"""Only deposit_credit takes GEN. Every other write method reverts when value is attached.

Direct Mode does not refuse value on a non-payable method by itself, so the contract checks it. Each
case builds a state in which the call would succeed, proves the call fails with value attached and
leaves the books untouched, then proves the very same call succeeds without value: the revert is
about the attached value and nothing else.
"""

import pytest

from tests.direct.support import (
    ARITH, BOND, BUDGET, BOUNTY_ID, COMMIT_BY, D_FEE, NOW_TS, REVEAL_BY, REWARDS, SUBJECT_HASH,
    SUBJECT_URL, Spec, addr, at, commit, deploy, deposit, open_bounty, reveal, run_to_review, slot_of,
)


def _withdraw_credit(contract, vm, actors):
    deposit(contract, vm, actors[1], 10**15)
    return actors[1], lambda: contract.withdraw_credit()


def _open_bounty(contract, vm, actors):
    deposit(contract, vm, actors[0], BUDGET)
    return actors[0], lambda: contract.open_bounty(
        BOUNTY_ID, "Handbook errata bounty", SUBJECT_URL, SUBJECT_HASH, *REWARDS, BUDGET, BOND, 8,
        COMMIT_BY, REVEAL_BY)


def _cancel_bounty(contract, vm, actors):
    open_bounty(contract, vm, actors[0])
    return actors[0], lambda: contract.cancel_bounty(BOUNTY_ID)


def _sealed(contract, vm, actors):
    open_bounty(contract, vm, actors[0])
    vm.warp(at(NOW_TS + 60))
    spec = Spec("sealed", actors[1], ARITH, D_FEE)
    return spec


def _commit_finding(contract, vm, actors):
    spec = _sealed(contract, vm, actors)
    deposit(contract, vm, spec.who, BOND)
    return spec.who, lambda: contract.commit_finding(addr(actors[0]), BOUNTY_ID, spec.seal(actors[0]))


def _withdraw_finding(contract, vm, actors):
    spec = _sealed(contract, vm, actors)
    commit(contract, vm, actors[0], spec)
    return spec.who, lambda: contract.withdraw_finding(addr(actors[0]), BOUNTY_ID, slot_of(spec, actors[0]))


def _reveal_finding(contract, vm, actors):
    spec = _sealed(contract, vm, actors)
    commit(contract, vm, actors[0], spec)
    vm.warp(at(COMMIT_BY))
    return spec.who, lambda: contract.reveal_finding(
        addr(actors[0]), BOUNTY_ID, slot_of(spec, actors[0]), spec.kind, spec.quote_a, spec.quote_b,
        spec.note, spec.mirror, spec.salt)


def _review_finding(contract, vm, actors):
    spec = Spec("sealed", actors[1], ARITH, D_FEE)
    run_to_review(contract, vm, actors[0], [spec])
    return actors[0], lambda: contract.review_finding(addr(actors[0]), BOUNTY_ID, slot_of(spec, actors[0]))


def _finalize_bounty(contract, vm, actors):
    open_bounty(contract, vm, actors[0])
    vm.warp(at(REVEAL_BY))
    return actors[5], lambda: contract.finalize_bounty(addr(actors[0]), BOUNTY_ID)


CASES = {
    "withdraw_credit": _withdraw_credit,
    "open_bounty": _open_bounty,
    "cancel_bounty": _cancel_bounty,
    "commit_finding": _commit_finding,
    "withdraw_finding": _withdraw_finding,
    "reveal_finding": _reveal_finding,
    "review_finding": _review_finding,
    "finalize_bounty": _finalize_bounty,
}


@pytest.mark.parametrize("value", [1, 10**15])  # one wei is as refused as a large amount
@pytest.mark.parametrize("name", sorted(CASES))
def test_attached_value_is_refused_by_every_method_except_deposit_credit(
    direct_vm, direct_deploy, actors, name, value
):
    contract = deploy(direct_vm, direct_deploy)
    sender, call = CASES[name](contract, direct_vm, actors)
    before = contract.get_accounting()
    direct_vm.sender = sender
    direct_vm.value = value
    with direct_vm.expect_revert("does not accept GEN"):
        call()
    direct_vm.value = 0
    assert contract.get_accounting() == before, "a refused call must not move anything"
    call()  # the identical call works without value, so the refusal was about the value alone


def test_every_write_method_is_covered_by_this_file():
    import re
    from pathlib import Path
    source = Path("contracts/errata.py").read_text()
    writes = re.findall(r"@gl\.public\.write(\.payable)?\n    def (\w+)\(", source)
    non_payable = sorted(name for payable, name in writes if not payable)
    assert non_payable == sorted(CASES), "a new write method needs a value-guard case"


def test_deposit_credit_is_the_one_payable_path(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    deposit(contract, direct_vm, actors[1], 7 * 10**14)
    assert contract.get_available_credit(addr(actors[1])) == 7 * 10**14
    assert contract.get_available_credit(addr(actors[2])) == 0
    assert contract.get_accounting()["total_credits"] == 7 * 10**14
