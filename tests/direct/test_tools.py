"""The client helpers must agree with the contract byte for byte."""

import pytest

from scripts import errata_tools as tools
from tests.direct.support import (
    AMBIGUOUS, ARITH, COMMIT_BY, D_FEE, D_MEETING, D_MEMBER, MISSING, NOW_TS, OK_HALL, OVERLAPPING,
    REVEAL_BY, SUBJECT, SUBJECT_HASH, Spec, at, commit, deploy, finding_of, open_bounty, review,
    reveal, serve,
)

CASES = {
    "valid_a": (D_FEE, "ok"),
    "valid_b": (D_MEMBER, "ok"),
    "valid_decoy": (OK_HALL, "ok"),
    "missing_a": (MISSING, "disqualified:quote_a_not_found"),
    "missing_b": ((D_FEE[0], "no such passage anywhere"), "disqualified:quote_b_not_found"),
    "ambiguous_a": (AMBIGUOUS, "disqualified:quote_a_ambiguous"),
    "ambiguous_b": ((D_MEETING[0], "annual fee"), "disqualified:quote_b_ambiguous"),
    "overlap": (OVERLAPPING, "disqualified:quotes_overlap"),
    "case": ((D_FEE[0].upper(), D_FEE[1]), "disqualified:quote_a_not_found"),
    "too_short": (("short", D_FEE[1]), "unrevealable"),
    "too_long": (("x" * 121, D_FEE[1]), "unrevealable"),
    "contained": (("annual fee is 120", "The annual fee is 120 dollars"), "unrevealable"),
    "identical": ((D_FEE[0], D_FEE[0]), "unrevealable"),
    "whitespace_ok": (("The   annual \n fee is 120 dollars", D_FEE[0]), "ok"),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_client_verdict_on_quotes_matches_the_contracts(direct_vm, direct_deploy, actors, name):
    pair, expected = CASES[name]
    problems = tools.check_quotes(SUBJECT, *pair)
    contract = deploy(direct_vm, direct_deploy)
    spec = Spec("case", actors[1], ARITH, pair)
    open_bounty(contract, direct_vm, actors[0])
    direct_vm.warp(at(NOW_TS + 60))
    commit(contract, direct_vm, actors[0], spec)
    direct_vm.warp(at(COMMIT_BY))
    if expected == "unrevealable":
        assert problems, "the client must warn about quotes the contract will refuse"
        with direct_vm.expect_revert():
            reveal(contract, direct_vm, actors[0], spec)
        return
    reveal(contract, direct_vm, actors[0], spec)
    serve(direct_vm, [spec])
    direct_vm.warp(at(REVEAL_BY))
    review(contract, direct_vm, actors[0], spec)
    state = finding_of(contract, actors[0], spec)
    if expected == "ok":
        assert problems == []
        assert state["status"] == "upheld"
    else:
        assert problems, "the client must warn about quotes the contract will disqualify"
        assert state["status"] == "disqualified"
        assert state["reason"] == "evidence:" + expected.split(":")[1]


def test_the_client_sha256_matches_the_pinned_hash():
    assert tools.sha256_hex(SUBJECT) == SUBJECT_HASH


def test_salts_are_fresh_valid_and_unique():
    salts = {tools.new_salt() for _ in range(50)}
    assert len(salts) == 50
    assert all(tools.SALT_PATTERN.fullmatch(s) for s in salts)


@pytest.mark.parametrize("salt", ["", "short", "has space in it........", "x" * 65, "bad/slash......."])
def test_commitment_rejects_invalid_salts(salt):
    with pytest.raises(ValueError):
        tools.commitment("0x" + "1" * 40, "B", "0x" + "2" * 40, 1, "a" * 8, "b" * 8, "", "", salt)


def test_commitment_rejects_unknown_kinds():
    with pytest.raises(ValueError):
        tools.commitment("0x" + "1" * 40, "B", "0x" + "2" * 40, 9, "a" * 8, "b" * 8, "", "", "s" * 20)


def test_commitment_is_case_insensitive_on_addresses_only():
    args = (1, "alpha beta", "gamma delta", "", "", "s" * 20)
    lower = tools.commitment("0x" + "ab" * 20, "B", "0x" + "cd" * 20, *args)
    upper = tools.commitment("0x" + "AB" * 20, "B", "0x" + "CD" * 20, *args)
    assert lower == upper
    assert tools.commitment("0x" + "ab" * 20, "b", "0x" + "cd" * 20, *args) != lower


def test_subject_limits():
    with pytest.raises(ValueError):
        tools.subject_text(b"")
    with pytest.raises(ValueError):
        tools.subject_text(b"x" * 100_001)
    assert tools.subject_text(b" a \n b ") == "a b"
    problems = tools.check_quotes(b"\xff\xfe", "abcdefgh", "ijklmnop")
    assert problems and "cannot be read" in problems[0]
    assert tools.check_quotes(b"   ", "abcdefgh", "ijklmnop")


def test_the_command_line_hash_check_and_seal(tmp_path, capsys):
    document = tmp_path / "handbook.md"
    document.write_bytes(SUBJECT)
    assert tools._main(["hash", str(document)]) == 0
    assert capsys.readouterr().out.strip() == SUBJECT_HASH

    assert tools._main(["check", str(document), "--quote-a", D_FEE[0], "--quote-b", D_FEE[1]]) == 0
    assert "OK" in capsys.readouterr().out
    assert tools._main(["check", str(document), "--quote-a", MISSING[0], "--quote-b", D_FEE[1]]) == 1
    assert "not found" in capsys.readouterr().out

    fixed_salt = "s" * 32
    publisher, finder = "0x" + "1" * 40, "0x" + "2" * 40
    code = tools._main(["seal", str(document), "--subject-hash", SUBJECT_HASH, "--publisher", publisher,
                        "--bounty-id", "B-1", "--finder", finder, "--kind", "1", "--quote-a", D_FEE[0],
                        "--quote-b", D_FEE[1], "--salt", fixed_salt])
    out = capsys.readouterr().out
    assert code == 0
    expected = tools.commitment(publisher, "B-1", finder, 1, D_FEE[0], D_FEE[1], "", "", fixed_salt)
    assert expected in out and SUBJECT_HASH in out

    code = tools._main(["seal", str(document), "--subject-hash", SUBJECT_HASH, "--publisher", publisher,
                        "--bounty-id", "B-1", "--finder", finder, "--kind", "1", "--quote-a", MISSING[0],
                        "--quote-b", D_FEE[1]])
    captured = capsys.readouterr()
    assert code == 1 and "Refusing" in captured.err

    code = tools._main(["seal", str(document), "--subject-hash", SUBJECT_HASH, "--publisher", publisher,
                        "--bounty-id", "B-1", "--finder", finder, "--kind", "1", "--quote-a", D_FEE[0],
                        "--quote-b", D_FEE[1], "--note", "n" * 241])
    assert code == 1


def test_a_document_that_is_not_the_pinned_one_is_refused_before_sealing(tmp_path, capsys):
    document = tmp_path / "handbook.md"
    document.write_bytes(SUBJECT.replace(b"120 dollars", b"100 dollars"))   # the publisher served other text
    publisher, finder = "0x" + "1" * 40, "0x" + "2" * 40
    code = tools._main(["seal", str(document), "--subject-hash", SUBJECT_HASH, "--publisher", publisher,
                        "--bounty-id", "B-1", "--finder", finder, "--kind", "1", "--quote-a", D_FEE[0],
                        "--quote-b", "The annual fee is 100 dollars"])
    captured = capsys.readouterr()
    assert code == 1
    assert "not to the bounty's pinned" in captured.err
    # seal cannot be run without naming the pinned hash at all
    with pytest.raises(SystemExit):
        tools._main(["seal", str(document), "--publisher", publisher, "--bounty-id", "B-1",
                     "--finder", finder, "--kind", "1", "--quote-a", D_FEE[0], "--quote-b", D_FEE[1]])
    # check accepts the hash optionally and normalizes its case
    assert tools.check_quotes(SUBJECT, *D_FEE, expected_hash=SUBJECT_HASH.upper().replace("0X", "0x")) == []
    assert tools.check_quotes(SUBJECT, *D_FEE, expected_hash="0x" + "0" * 64)


def _seal(document, capsys, **overrides):
    args = {"publisher": "0x" + "1" * 40, "bounty_id": "B-1", "finder": "0x" + "2" * 40, "mirror": ""}
    args.update(overrides)
    # The "--option=value" form keeps argparse from reading a value that starts with "-" as a flag.
    argv = ["seal", str(document), "--subject-hash", SUBJECT_HASH, f"--publisher={args['publisher']}",
            f"--bounty-id={args['bounty_id']}", f"--finder={args['finder']}", "--kind", "1",
            "--quote-a", D_FEE[0], "--quote-b", D_FEE[1], "--salt", "s" * 32, f"--mirror={args['mirror']}"]
    code = tools._main(argv)
    return code, capsys.readouterr()


@pytest.mark.parametrize("mirror", [
    "http://mirror.example.org/h.md",
    "https://mirror.example.org:8443/h.md",
    "https://mirror.example.org/h.md#top",
    "https://localhost/h.md",
    "https://93.184.216.34/h.md",
])
def test_seal_refuses_a_mirror_the_contract_would_never_let_the_finder_reveal(tmp_path, capsys, mirror):
    document = tmp_path / "handbook.md"
    document.write_bytes(SUBJECT)
    code, captured = _seal(document, capsys, mirror=mirror)
    assert code == 1
    assert "mirror" in captured.err and "commitment:" not in captured.out


def test_seal_accepts_and_normalizes_a_valid_mirror_and_surrounding_spaces(tmp_path, capsys):
    document = tmp_path / "handbook.md"
    document.write_bytes(SUBJECT)
    code, captured = _seal(document, capsys, mirror="  https://mirror.example.org/h.md ",
                           bounty_id=" B-1 ", publisher=" 0x" + "1" * 40 + " ")
    expected = tools.commitment("0x" + "1" * 40, "B-1", "0x" + "2" * 40, 1, D_FEE[0], D_FEE[1], "",
                                "https://mirror.example.org/h.md", "s" * 32)
    assert code == 0 and expected in captured.out


@pytest.mark.parametrize("override", [
    {"publisher": "0x" + "1" * 39},
    {"finder": "2" * 40},
    {"finder": "0x" + "g" * 40},
    {"bounty_id": "bad id"},
    {"bounty_id": "-leading-dash"},
    {"bounty_id": "x" * 65},
])
def test_seal_refuses_inputs_the_contract_would_parse_differently(tmp_path, capsys, override):
    document = tmp_path / "handbook.md"
    document.write_bytes(SUBJECT)
    code, captured = _seal(document, capsys, **override)
    assert code == 1 and "commitment:" not in captured.out

