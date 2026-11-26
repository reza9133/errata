"""Input validation: every malformed call must revert and leave the books untouched."""

import pytest

from tests.direct.support import (
    slot_of,
    ARITH, BOND, BUDGET, COMMIT_BY, CONTRA, D_FEE, D_MEETING, DAY, DEFN, MIN_BOND, MIN_BUDGET,
    MIN_REWARD, NOW_TS, REVEAL_BY, REWARDS, SUBJECT_HASH, SUBJECT_URL, Spec, addr, at,
    bounty_of, check_books, commit, credit, deploy, deposit, finding_of, open_bounty,
)

BOUNTY = "B-001"


def _raw_open(contract, vm, publisher, **kw):
    params = dict(
        bounty_id=BOUNTY, title="Title", url=SUBJECT_URL, subject_hash=SUBJECT_HASH,
        r1=REWARDS[0], r2=REWARDS[1], r3=REWARDS[2], budget=BUDGET, bond=BOND, capacity=8,
        commit_by=COMMIT_BY, reveal_by=REVEAL_BY,
    )
    params.update(kw)
    vm.sender = publisher
    return contract.open_bounty(
        params["bounty_id"], params["title"], params["url"], params["subject_hash"],
        params["r1"], params["r2"], params["r3"], params["budget"], params["bond"],
        params["capacity"], params["commit_by"], params["reveal_by"],
    )


BAD_OPEN = {
    "empty_id": dict(bounty_id=""),
    "long_id": dict(bounty_id="a" * 65),
    "id_with_space": dict(bounty_id="bad id"),
    "id_leading_dash": dict(bounty_id="-x"),
    "id_hash_char": dict(bounty_id="a#b"),
    "id_colon": dict(bounty_id="a:b"),
    "empty_title": dict(title="   "),
    "long_title": dict(title="t" * 121),
    "short_hash": dict(subject_hash="0x1234"),
    "no_prefix_hash": dict(subject_hash="ab" * 32),
    "non_hex_hash": dict(subject_hash="0x" + "g" * 64),
    "http_url": dict(url="http://docs.example.org/h.md"),
    "localhost": dict(url="https://localhost/h.md"),
    "ipv4": dict(url="https://127.0.0.1/h.md"),
    "private_ip": dict(url="https://10.0.0.5/h.md"),
    "ipv6": dict(url="https://[::1]/h.md"),
    "credentials": dict(url="https://user:pw@docs.example.org/h.md"),
    "odd_port": dict(url="https://docs.example.org:8443/h.md"),
    "fragment": dict(url="https://docs.example.org/h.md#x"),
    "dot_local": dict(url="https://printer.local/h.md"),
    "dot_internal": dict(url="https://svc.internal/h.md"),
    "single_label": dict(url="https://intranet/h.md"),
    "space_in_url": dict(url="https://docs.example.org/a b.md"),
    "control_in_url": dict(url="https://docs.example.org/a\x01b.md"),
    "long_url": dict(url="https://docs.example.org/" + "a" * 520),
    "no_rewards": dict(r1=0, r2=0, r3=0),
    "reward_below_min": dict(r1=MIN_REWARD - 1),
    "other_reward_below_min": dict(r3=1),
    "budget_below_min": dict(budget=MIN_BUDGET - 1),
    "budget_below_largest_reward": dict(r2=2 * 10**15, budget=15 * 10**14),
    "bond_below_min": dict(bond=MIN_BOND - 1),
    "bond_above_smallest_reward": dict(bond=REWARDS[2] + 1),
    "bond_above_only_reward": dict(r1=0, r3=0, r2=REWARDS[1], bond=REWARDS[1] + 1),
    "zero_capacity": dict(capacity=0),
    "capacity_over_limit": dict(capacity=17),
    "u16_overflow_capacity": dict(capacity=70_000),
    "commit_too_soon": dict(commit_by=NOW_TS + 119),
    "commit_in_past": dict(commit_by=NOW_TS - 1),
    "commit_too_far": dict(commit_by=NOW_TS + 30 * DAY + 1),
    "reveal_too_soon": dict(reveal_by=COMMIT_BY + 119),
    "reveal_before_commit": dict(reveal_by=COMMIT_BY - 1),
    "reveal_too_far": dict(reveal_by=COMMIT_BY + 7 * DAY + 1),
}


@pytest.mark.parametrize("name", sorted(BAD_OPEN))
def test_a_malformed_bounty_reverts_and_changes_nothing(direct_vm, direct_deploy, actors, name):
    contract = deploy(direct_vm, direct_deploy)
    deposit(contract, direct_vm, actors[0], 10 * BUDGET)
    before = contract.get_accounting()
    with direct_vm.expect_revert():
        _raw_open(contract, direct_vm, actors[0], **BAD_OPEN[name])
    assert contract.get_accounting() == before
    assert credit(contract, actors[0]) == 10 * BUDGET
    with direct_vm.expect_revert():
        bounty_of(contract, actors[0])


GOOD_OPEN = {
    "only_arithmetic": dict(r2=0, r3=0, bond=REWARDS[0]),
    "only_contradiction": dict(r1=0, r3=0),
    "only_definition": dict(r1=0, r2=0, bond=REWARDS[2]),
    "minimum_everything": dict(r1=MIN_REWARD, r2=0, r3=0, budget=MIN_BUDGET, bond=MIN_BOND),
    "bond_equals_smallest_reward": dict(bond=REWARDS[2]),
    "budget_equals_largest_reward": dict(r1=0, r3=0, r2=MIN_BUDGET, budget=MIN_BUDGET),
    "capacity_one": dict(capacity=1),
    "capacity_sixteen": dict(capacity=16),
    "earliest_commit": dict(commit_by=NOW_TS + 120, reveal_by=NOW_TS + 240),
    "latest_commit": dict(commit_by=NOW_TS + 30 * DAY, reveal_by=NOW_TS + 30 * DAY + 7 * DAY),
    "title_at_limit": dict(title="t" * 120),
    "id_at_limit": dict(bounty_id="a" * 64),
    "id_with_symbols": dict(bounty_id="Edition_7.v2-final"),
    "uppercase_hash": dict(subject_hash="0x" + SUBJECT_HASH[2:].upper()),
    "padded_title": dict(title="  spaced    title  "),
}


@pytest.mark.parametrize("name", sorted(GOOD_OPEN))
def test_boundary_values_are_accepted(direct_vm, direct_deploy, actors, name):
    contract = deploy(direct_vm, direct_deploy)
    deposit(contract, direct_vm, actors[0], 10 * BUDGET)
    kwargs = GOOD_OPEN[name]
    _raw_open(contract, direct_vm, actors[0], **kwargs)
    key = kwargs.get("bounty_id", BOUNTY)
    bounty = contract.get_bounty(addr(actors[0]), key)
    assert bounty["status"] == "open"
    assert credit(contract, actors[0]) == 10 * BUDGET - kwargs.get("budget", BUDGET)
    check_books(contract, actors[:10], [(actors[0], key)])


def test_title_whitespace_is_collapsed_and_hash_is_lowercased(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    deposit(contract, direct_vm, actors[0], BUDGET)
    _raw_open(contract, direct_vm, actors[0], title="  spaced    title  ",
              subject_hash="0x" + SUBJECT_HASH[2:].upper())
    bounty = bounty_of(contract, actors[0])
    assert bounty["title"] == "spaced title"
    assert bounty["subject_hash"] == SUBJECT_HASH


def test_opening_requires_available_credit_and_a_fresh_id(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    with direct_vm.expect_revert():
        _raw_open(contract, direct_vm, actors[0])  # no credit at all
    deposit(contract, direct_vm, actors[0], BUDGET - 1)
    with direct_vm.expect_revert():
        _raw_open(contract, direct_vm, actors[0])  # one wei short
    deposit(contract, direct_vm, actors[0], 1)
    _raw_open(contract, direct_vm, actors[0])
    deposit(contract, direct_vm, actors[0], BUDGET)
    with direct_vm.expect_revert():
        _raw_open(contract, direct_vm, actors[0])  # duplicate id
    assert credit(contract, actors[0]) == BUDGET
    # The same id under another publisher is a different bounty.
    deposit(contract, direct_vm, actors[1], BUDGET)
    _raw_open(contract, direct_vm, actors[1])
    assert bounty_of(contract, actors[1])["publisher"].lower() == addr(actors[1])


def test_deposits_must_be_positive(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    direct_vm.sender = actors[0]
    direct_vm.value = 0
    with direct_vm.expect_revert():
        contract.deposit_credit()
    assert contract.get_accounting()["total_credits"] == 0


def test_withdrawing_with_no_credit_reverts(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    direct_vm.sender = actors[0]
    with direct_vm.expect_revert():
        contract.withdraw_credit()
    deposit(contract, direct_vm, actors[0], 7)
    contract.withdraw_credit()
    with direct_vm.expect_revert():
        contract.withdraw_credit()
    assert credit(contract, actors[0]) == 0


@pytest.mark.parametrize("bad", ["", "0x", "0x1234", "not an address", "0x" + "0" * 40])
def test_malformed_addresses_are_rejected_by_views(direct_vm, direct_deploy, actors, bad):
    contract = deploy(direct_vm, direct_deploy)
    open_bounty(contract, direct_vm, actors[0])
    with direct_vm.expect_revert():
        contract.get_available_credit(bad)
    with direct_vm.expect_revert():
        contract.get_bounty(bad, BOUNTY)


# ---------------------------------------------------------------- commit validation


def _setup_open(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    open_bounty(contract, direct_vm, actors[0])
    direct_vm.warp(at(NOW_TS + 60))
    return contract


@pytest.mark.parametrize("bad", ["", "0x1234", "ab" * 32, "0x" + "g" * 64, "0x" + "a" * 63])
def test_commit_rejects_malformed_commitments(direct_vm, direct_deploy, actors, bad):
    contract = _setup_open(direct_vm, direct_deploy, actors)
    deposit(contract, direct_vm, actors[1], BOND)
    direct_vm.sender = actors[1]
    with direct_vm.expect_revert():
        contract.commit_finding(addr(actors[0]), BOUNTY, bad)
    assert credit(contract, actors[1]) == BOND
    assert contract.get_accounting()["total_locked_bonds"] == 0


def test_commit_needs_the_full_bond_in_credit(direct_vm, direct_deploy, actors):
    contract = _setup_open(direct_vm, direct_deploy, actors)
    spec = Spec("poor", actors[1], ARITH, D_FEE)
    deposit(contract, direct_vm, spec.who, BOND - 1)
    with direct_vm.expect_revert():
        commit(contract, direct_vm, actors[0], spec, topup=False)
    assert credit(contract, spec.who) == BOND - 1
    deposit(contract, direct_vm, spec.who, 1)
    commit(contract, direct_vm, actors[0], spec, topup=False)
    assert credit(contract, spec.who) == 0


def test_commit_to_unknown_or_closed_bounties_fails(direct_vm, direct_deploy, actors):
    contract = _setup_open(direct_vm, direct_deploy, actors)
    spec = Spec("late", actors[1], ARITH, D_FEE)
    deposit(contract, direct_vm, spec.who, BOND)
    direct_vm.sender = spec.who
    with direct_vm.expect_revert():
        contract.commit_finding(addr(actors[0]), "NOPE", spec.seal(actors[0]))
    with direct_vm.expect_revert():
        contract.commit_finding(addr(actors[2]), BOUNTY, spec.seal(actors[0]))
    direct_vm.warp(at(COMMIT_BY - 1))
    commit(contract, direct_vm, actors[0], spec, topup=False)  # last second still accepted
    late = Spec("later", actors[2], ARITH, D_FEE)
    direct_vm.warp(at(COMMIT_BY))
    with direct_vm.expect_revert():
        commit(contract, direct_vm, actors[0], late)


# ----------------------------------------------------------------- reveal validation


def _sealed(direct_vm, direct_deploy, actors, **spec_kwargs):
    contract = _setup_open(direct_vm, direct_deploy, actors)
    spec = Spec("subject", actors[1], spec_kwargs.pop("kind", ARITH),
                spec_kwargs.pop("pair", D_FEE), **spec_kwargs)
    commit(contract, direct_vm, actors[0], spec)
    direct_vm.warp(at(COMMIT_BY))
    return contract, spec


def _try_reveal(contract, vm, publisher, spec, **overrides):
    vm.sender = spec.who
    contract.reveal_finding(
        addr(publisher), BOUNTY, slot_of(spec, publisher, BOUNTY),
        overrides.get("kind", spec.kind), overrides.get("quote_a", spec.quote_a),
        overrides.get("quote_b", spec.quote_b), overrides.get("note", spec.note),
        overrides.get("mirror", spec.mirror), overrides.get("salt", spec.salt),
    )


BAD_REVEAL = {
    "kind_zero": dict(kind=0),
    "kind_four": dict(kind=4),
    "kind_changed": dict(kind=CONTRA),
    "quote_a_changed_by_a_char": dict(quote_a=D_FEE[0] + "!"),
    "quote_a_lowercased": dict(quote_a=D_FEE[0].lower() + "x"),
    "quote_b_changed": dict(quote_b="The annual fee is 121 dollars"),
    "quotes_swapped": dict(quote_a=D_FEE[1], quote_b=D_FEE[0]),
    "quote_a_too_short": dict(quote_a="short"),
    "quote_b_too_short": dict(quote_b="1234567"),
    "quote_a_too_long": dict(quote_a="x" * 121),
    "quote_b_too_long": dict(quote_b="y" * 121),
    "quote_a_blank": dict(quote_a="        "),
    "same_quotes": dict(quote_a=D_FEE[0], quote_b=D_FEE[0]),
    "a_inside_b": dict(quote_a="annual fee is 120", quote_b="The annual fee is 120 dollars"),
    "b_inside_a": dict(quote_a="The annual fee is 120 dollars", quote_b="annual fee is 120"),
    "note_added": dict(note="a note nobody sealed"),
    "mirror_added": dict(mirror="https://mirror.example.org/h.md"),
    "wrong_salt": dict(salt="x" * 40),
    "salt_too_short": dict(salt="short"),
    "salt_bad_chars": dict(salt="bad salt with spaces!!"),
}


@pytest.mark.parametrize("name", sorted(BAD_REVEAL))
def test_a_bad_reveal_reverts_and_keeps_the_finding_sealed(direct_vm, direct_deploy, actors, name):
    contract, spec = _sealed(direct_vm, direct_deploy, actors)
    with direct_vm.expect_revert():
        _try_reveal(contract, direct_vm, actors[0], spec, **BAD_REVEAL[name])
    state = finding_of(contract, actors[0], spec)
    assert state["status"] == "committed"
    assert state["quote_a"] == "" and state["kind"] == 0
    _try_reveal(contract, direct_vm, actors[0], spec)  # the honest reveal still works
    assert finding_of(contract, actors[0], spec)["status"] == "revealed"


def test_overlong_note_and_bad_mirror_are_rejected_when_sealed_consistently(
    direct_vm, direct_deploy, actors
):
    long_note = Spec("note", actors[1], ARITH, D_FEE, note="n" * 241)
    contract = _setup_open(direct_vm, direct_deploy, actors)
    commit(contract, direct_vm, actors[0], long_note)
    direct_vm.warp(at(COMMIT_BY))
    with direct_vm.expect_revert():
        _try_reveal(contract, direct_vm, actors[0], long_note)
    assert finding_of(contract, actors[0], long_note)["status"] == "committed"


@pytest.mark.parametrize("mirror", [
    "http://mirror.example.org/h.md", "https://localhost/h.md", "https://127.0.0.1/h.md",
    "https://u:p@mirror.example.org/h.md", "https://mirror.example.org:99/h.md",
    "https://mirror.example.org/h.md#frag", "https://intranet/h.md", "https://x.local/h.md",
])
def test_a_bad_mirror_cannot_be_revealed_even_when_sealed(direct_vm, direct_deploy, actors, mirror):
    contract = _setup_open(direct_vm, direct_deploy, actors)
    spec = Spec("mirror", actors[1], ARITH, D_FEE, mirror=mirror)
    commit(contract, direct_vm, actors[0], spec)
    direct_vm.warp(at(COMMIT_BY))
    with direct_vm.expect_revert():
        _try_reveal(contract, direct_vm, actors[0], spec)


def test_a_good_mirror_note_and_extra_whitespace_reveal_fine(direct_vm, direct_deploy, actors):
    contract = _setup_open(direct_vm, direct_deploy, actors)
    spec = Spec("fine", actors[1], DEFN, D_FEE,
                note="  The   instalments\nsum to 100.  ", mirror="https://mirror.example.org/h.md")
    commit(contract, direct_vm, actors[0], spec)
    direct_vm.warp(at(COMMIT_BY))
    _try_reveal(contract, direct_vm, actors[0], spec)
    state = finding_of(contract, actors[0], spec)
    assert state["note"] == "The instalments sum to 100."
    assert state["mirror_url"] == "https://mirror.example.org/h.md"
    assert state["kind_name"] == "definition_conflict"


def test_whitespace_inside_quotes_is_collapsed_before_sealing_and_after(
    direct_vm, direct_deploy, actors
):
    contract = _setup_open(direct_vm, direct_deploy, actors)
    messy = ("The   annual\nfee is   120 dollars", "payable in 4 equal quarterly\tinstalments of 25 dollars each")
    spec = Spec("messy", actors[1], ARITH, messy)
    commit(contract, direct_vm, actors[0], spec)
    direct_vm.warp(at(COMMIT_BY))
    _try_reveal(contract, direct_vm, actors[0], spec)
    state = finding_of(contract, actors[0], spec)
    assert state["quote_a"] == "The annual fee is 120 dollars"


def test_a_kind_that_pays_nothing_cannot_be_revealed(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    open_bounty(contract, direct_vm, actors[0], rewards=(REWARDS[0], 0, 0), bond=MIN_BOND)
    direct_vm.warp(at(NOW_TS + 60))
    spec = Spec("wrongkind", actors[1], CONTRA, D_MEETING)
    commit(contract, direct_vm, actors[0], spec, bond=MIN_BOND)
    direct_vm.warp(at(COMMIT_BY))
    with direct_vm.expect_revert():
        _try_reveal(contract, direct_vm, actors[0], spec)
    assert finding_of(contract, actors[0], spec)["status"] == "committed"
