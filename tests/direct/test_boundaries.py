"""Exact edge values for every length and format limit (both sides of each boundary)."""

import pytest

from tests.direct.support import (
    ARITH, COMMIT_BY, D_FEE, NOW_TS, SUBJECT_HASH, SUBJECT_TEXT, Spec, addr, at, deploy, deposit,
    finding_of, open_bounty, REWARDS, BUDGET, BOND, REVEAL_BY,
)


def _unique_slice(length, start_at=0):
    """A slice of the subject that is exactly `length` characters and occurs once."""
    for start in range(start_at, len(SUBJECT_TEXT) - length):
        piece = SUBJECT_TEXT[start:start + length]
        if piece == piece.strip() and SUBJECT_TEXT.count(piece) == 1:
            return piece
    raise AssertionError("no unique slice")


def _raw_commitment(publisher, bounty_id, spec):
    """The commitment algorithm WITHOUT the client's own salt validation, so that the
    contract's validation is what gets tested."""
    import hashlib
    from scripts.errata_tools import COMMITMENT_DOMAIN, clean
    fields = [COMMITMENT_DOMAIN, addr(publisher) + ":" + bounty_id, addr(spec.who), str(spec.kind),
              clean(spec.quote_a), clean(spec.quote_b), clean(spec.note), spec.mirror.strip(),
              spec.salt]
    data = b"".join(len(f.encode()).to_bytes(4, "big") + f.encode() for f in fields)
    return "0x" + hashlib.sha256(data).hexdigest()


def _try_reveal(direct_vm, direct_deploy, actors, spec_kwargs):
    contract = deploy(direct_vm, direct_deploy)
    open_bounty(contract, direct_vm, actors[0])
    direct_vm.warp(at(NOW_TS + 60))
    spec = Spec("edge", actors[1], ARITH, **spec_kwargs)
    deposit(contract, direct_vm, spec.who, BOND)
    direct_vm.sender = spec.who
    index = contract.commit_finding(addr(actors[0]), "B-001", _raw_commitment(actors[0], "B-001", spec))
    spec.slots[(addr(actors[0]), "B-001")] = index
    direct_vm.warp(at(COMMIT_BY))
    direct_vm.sender = spec.who
    try:
        contract.reveal_finding(addr(actors[0]), "B-001", index, spec.kind, spec.quote_a, spec.quote_b,
                                spec.note, spec.mirror, spec.salt)
    except Exception as error:  # noqa: BLE001
        assert type(error).__name__ == "UserError"
        return False
    return finding_of(contract, actors[0], spec)["status"] == "revealed"


@pytest.mark.parametrize("length,ok", [(7, False), (8, True), (9, True), (119, True), (120, True),
                                       (121, False)])
def test_quote_length_limits_on_both_sides(direct_vm, direct_deploy, actors, length, ok):
    quote_a = _unique_slice(length)
    quote_b = D_FEE[1] if quote_a not in D_FEE[1] and D_FEE[1] not in quote_a else "Section 5. The assembly"
    padded = quote_a if length <= 120 else quote_a + "x" * 0
    if length == 121:
        padded = _unique_slice(121)
    assert _try_reveal(direct_vm, direct_deploy, actors,
                       dict(pair=(padded, quote_b))) is ok


@pytest.mark.parametrize("note_length,ok", [(0, True), (239, True), (240, True), (241, False)])
def test_note_length_limits(direct_vm, direct_deploy, actors, note_length, ok):
    assert _try_reveal(direct_vm, direct_deploy, actors,
                       dict(pair=D_FEE, note="n" * note_length)) is ok


@pytest.mark.parametrize("salt,ok", [("s" * 15, False), ("s" * 16, True), ("s" * 64, True),
                                     ("s" * 65, False), ("a-b_c.d~e" * 2, True)])
def test_salt_length_and_alphabet(direct_vm, direct_deploy, actors, salt, ok):
    assert _try_reveal(direct_vm, direct_deploy, actors, dict(pair=D_FEE, salt=salt)) is ok


@pytest.mark.parametrize("mirror,ok", [
    ("https://mirror.example.org/h.md", True),
    ("https://mirror.example.org:443/h.md", True),
    ("https://mirror.example.org:80/h.md", False),
    ("https://MIRROR.Example.org/h.md", True),
    ("https://sub.domain.mirror.example.org/a/b/c.md?x=1", True),
    ("https://-bad.example.org/h.md", False),
    ("https://bad-.example.org/h.md", False),
    ("https://example.123/h.md", False),
    ("https://example.c/h.md", False),
    ("https://" + "a" * 64 + ".example.org/h.md", False),
    ("http://mirror.example.org/h.md", False),
    ("https://mirror.example.org/h.md#top", False),
    ("https://user:pw@mirror.example.org/h.md", False),
    ("https://localhost/h.md", False),
    ("https://files.internal/h.md", False),
    ("https://93.184.216.34/h.md", False),
    ("https://mirror.example.org/h m.md", False),
    ("https://mirror/h.md", False),
    ("ftp://mirror.example.org/h.md", False),
    ("  https://mirror.example.org/h.md  ", True),
])
def test_mirror_url_format_edges(direct_vm, direct_deploy, actors, mirror, ok):
    assert _try_reveal(direct_vm, direct_deploy, actors, dict(pair=D_FEE, mirror=mirror)) is ok
    # The client must refuse exactly what the contract refuses: the mirror is sealed inside the
    # commitment, so a mirror that cannot be revealed would forfeit the bond.
    from scripts.errata_tools import mirror_problem
    assert (mirror_problem(mirror) == "") is ok


def test_the_url_length_limit_is_exact(direct_vm, direct_deploy, actors):
    base = "https://docs.example.org/"
    exact = base + "a" * (512 - len(base))
    over = exact + "a"
    contract = deploy(direct_vm, direct_deploy)
    deposit(contract, direct_vm, actors[0], 2 * BUDGET)
    direct_vm.sender = actors[0]
    contract.open_bounty("ok", "t", exact, SUBJECT_HASH, *REWARDS, BUDGET, BOND, 4,
                         COMMIT_BY, REVEAL_BY)
    with direct_vm.expect_revert():
        contract.open_bounty("over", "t", over, SUBJECT_HASH, *REWARDS, BUDGET, BOND, 4,
                             COMMIT_BY, REVEAL_BY)


def test_a_primary_url_with_an_explicit_default_port_is_accepted(direct_vm, direct_deploy, actors):
    contract = deploy(direct_vm, direct_deploy)
    deposit(contract, direct_vm, actors[0], BUDGET)
    direct_vm.sender = actors[0]
    contract.open_bounty("p", "t", "https://docs.example.org:443/h.md", SUBJECT_HASH, *REWARDS,
                         BUDGET, BOND, 4, COMMIT_BY, REVEAL_BY)
    assert contract.get_bounty(addr(actors[0]), "p")["subject_url"] == "https://docs.example.org:443/h.md"
