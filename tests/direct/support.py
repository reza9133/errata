"""Shared scenario helpers for the Errata Direct Mode tests."""

import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.errata_tools import clean  # noqa: E402
from scripts.errata_tools import commitment as client_commitment  # noqa: E402
from scripts.errata_tools import sha256_hex  # noqa: E402

CONTRACT = "contracts/errata.py"
RUNNER = "v0.6.0-rc16"
NOW = "2026-10-01T12:00:00Z"
NOW_TS = int(datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc).timestamp())
HOUR = 3600
DAY = 24 * HOUR

ARITH, CONTRA, DEFN = 1, 2, 3
KIND_WORD = {ARITH: "ARITHMETIC", CONTRA: "CONTRADICTION", DEFN: "DEFINITION_CONFLICT"}

REWARDS = (4 * 10**14, 5 * 10**14, 3 * 10**14)
BUDGET = 15 * 10**14
BOND = 10**14
MIN_BUDGET = 10**15
MIN_BOND = 10**13
MIN_REWARD = 10**14
COMMIT_BY = NOW_TS + HOUR
REVEAL_BY = COMMIT_BY + HOUR
REVIEW_BY = REVEAL_BY + 3 * DAY
BOUNTY_ID = "B-001"

SUBJECT_URL = "https://docs.example.org/handbook.md"
SUBJECT = (
    "Harborview Cooperative Handbook, edition 7.\n"
    "\n"
    "Section 1. In this handbook \"Member\" means a person who has paid the annual fee and "
    "whose membership is currently active.\n"
    "Section 2. The annual fee is 120 dollars, payable in 4 equal quarterly instalments of "
    "25 dollars each.\n"
    "Section 3. The assembly meets on the first Monday of every month.\n"
    "Section 4. Minutes are published within 7 days of each assembly meeting.\n"
    "Section 5. The assembly does not meet in August because the hall is closed for repairs.\n"
    "Section 6. Only Members may vote, and a person whose fee is unpaid may still vote as a "
    "Member at the assembly.\n"
    "Section 7. The hall has 4 rows and each of the 4 rows holds 20 chairs, so the hall seats "
    "80 people.\n"
    "Section 8. Late fees are 5 percent of the unpaid balance, so on an unpaid balance of "
    "200 dollars the late fee is 20 dollars.\n"
).encode()
SUBJECT_TEXT = clean(SUBJECT.decode())

# Planted defects (anchor pairs) and decoys, all verified against SUBJECT_TEXT below.
D_FEE = ("payable in 4 equal quarterly instalments of 25 dollars each",
         "The annual fee is 120 dollars")
D_FEE_VARIANT = ("4 equal quarterly instalments of 25 dollars", "annual fee is 120 dollars")
D_MEETING = ("The assembly meets on the first Monday of every month",
             "The assembly does not meet in August")
D_MEMBER = ('"Member" means a person who has paid the annual fee',
            "a person whose fee is unpaid may still vote as a Member at the assembly")
D_LATE = ("on an unpaid balance of 200 dollars the late fee is 20 dollars",
          "Late fees are 5 percent of the unpaid balance")
OK_HALL = ("each of the 4 rows holds 20 chairs", "so the hall seats 80 people")
OK_MINUTES = ("Minutes are published within 7 days of each assembly meeting",
              "The assembly meets on the first Monday of every month")
MISSING = ("The assembly meets every second Tuesday", "The assembly does not meet in August")
AMBIGUOUS = ("annual fee", "The assembly does not meet in August")
OVERLAPPING = ("The assembly meets on the first Monday", "first Monday of every month")

for _pair in (D_FEE, D_FEE_VARIANT, D_MEETING, D_MEMBER, D_LATE, OK_HALL, OK_MINUTES, OVERLAPPING):
    for _quote in _pair:
        assert SUBJECT_TEXT.count(_quote) == 1, _quote
assert SUBJECT_TEXT.count(AMBIGUOUS[0]) == 2


def span_of(quote):
    start = SUBJECT_TEXT.find(clean(quote))
    return [start, start + len(clean(quote))]


def at(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def addr(value):
    return "0x" + value.hex().lower().removeprefix("0x")


def digest(raw):
    return sha256_hex(raw)


SUBJECT_HASH = digest(SUBJECT)


def llm_pattern(kind, quote_a, quote_b, radius=None):
    """Matches the one prompt built for this exact (kind, quote A, quote B[, context radius])."""
    prefix = (r"cut off at about " + str(radius) + r" characters.*") if radius else ""
    return (
        r"(?s)" + prefix + r"Claimed defect kind: " + KIND_WORD[kind] + r":"
        + r".*BEGIN_UNTRUSTED_EXCERPT_A\n[^\n]*\[\[\[" + re.escape(clean(quote_a)) + r"\]\]\][^\n]*"
        + r"\nEND_UNTRUSTED_EXCERPT_A\nBEGIN_UNTRUSTED_EXCERPT_B\n[^\n]*\[\[\["
        + re.escape(clean(quote_b)) + r"\]\]\]"
    )


def verdict(name="upheld", reason="The passages were compared."):
    return {"verdict": name, "reason": reason}


@dataclass
class Spec:
    """One finder's sealed finding plus the model answer it will receive."""

    name: str
    who: bytes
    kind: int
    pair: tuple
    answer: object = "upheld"
    note: str = ""
    mirror: str = ""
    salt: str = ""
    extra: dict = field(default_factory=dict)
    # (publisher, bounty id) -> index the contract gave this finding when it was sealed
    slots: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.salt:
            self.salt = hashlib.sha256(f"salt-{self.name}".encode()).hexdigest()[:40]

    @property
    def quote_a(self):
        return self.pair[0]

    @property
    def quote_b(self):
        return self.pair[1]

    def pattern(self):
        return llm_pattern(self.kind, self.quote_a, self.quote_b)

    def answer_json(self):
        value = self.answer
        if isinstance(value, str):
            value = verdict(value)
        return json.dumps(value)

    def seal(self, publisher, bounty_id=BOUNTY_ID, **overrides):
        return client_commitment(
            addr(publisher), bounty_id, addr(self.who),
            overrides.get("kind", self.kind),
            overrides.get("quote_a", self.quote_a),
            overrides.get("quote_b", self.quote_b),
            overrides.get("note", self.note),
            overrides.get("mirror", self.mirror),
            overrides.get("salt", self.salt),
        )


def deploy(direct_vm, direct_deploy):
    direct_vm.warp(NOW)
    return direct_deploy(CONTRACT, sdk_version=RUNNER)


def deposit(contract, direct_vm, who, amount):
    direct_vm.sender = who
    direct_vm.value = amount
    assert contract.deposit_credit() == amount
    direct_vm.value = 0


def open_bounty(contract, direct_vm, publisher, bounty_id=BOUNTY_ID, *, rewards=REWARDS,
                budget=BUDGET, bond=BOND, max_findings=8, commit_by=COMMIT_BY,
                reveal_by=REVEAL_BY, title="Handbook errata bounty", url=SUBJECT_URL,
                subject_hash=None, fund=True):
    if fund:
        deposit(contract, direct_vm, publisher, budget)
    direct_vm.sender = publisher
    return contract.open_bounty(
        bounty_id, title, url,
        subject_hash if subject_hash is not None else SUBJECT_HASH,
        rewards[0], rewards[1], rewards[2], budget, bond, max_findings, commit_by, reveal_by,
    )


def commit(contract, direct_vm, publisher, spec, bounty_id=BOUNTY_ID, bond=BOND, topup=True):
    if topup:
        deposit(contract, direct_vm, spec.who, bond)
    direct_vm.sender = spec.who
    index = contract.commit_finding(addr(publisher), bounty_id, spec.seal(publisher, bounty_id))
    spec.slots[(addr(publisher), bounty_id)] = index
    return index


def slot_of(spec, publisher, bounty_id=BOUNTY_ID):
    """The index the contract assigned to this spec's finding in this bounty."""
    return spec.slots[(addr(publisher), bounty_id)]


def reveal(contract, direct_vm, publisher, spec, bounty_id=BOUNTY_ID, **overrides):
    direct_vm.sender = spec.who
    contract.reveal_finding(
        addr(publisher), bounty_id, slot_of(spec, publisher, bounty_id),
        overrides.get("kind", spec.kind),
        overrides.get("quote_a", spec.quote_a),
        overrides.get("quote_b", spec.quote_b),
        overrides.get("note", spec.note),
        overrides.get("mirror", spec.mirror),
        overrides.get("salt", spec.salt),
    )


def serve(direct_vm, specs, subject=SUBJECT, url=SUBJECT_URL):
    """Mock the web and the model for every spec. Patterns are keyed on the exact quotes."""
    direct_vm.mock_web(re.escape(url), {"status": 200, "body": subject})
    for spec in specs:
        direct_vm.mock_llm(spec.pattern(), spec.answer_json())


def review(contract, direct_vm, publisher, spec, caller=None, bounty_id=BOUNTY_ID):
    direct_vm.sender = caller if caller is not None else publisher
    contract.review_finding(addr(publisher), bounty_id, slot_of(spec, publisher, bounty_id))


def finalize(contract, direct_vm, caller, publisher, bounty_id=BOUNTY_ID):
    direct_vm.sender = caller
    contract.finalize_bounty(addr(publisher), bounty_id)


def credit(contract, who):
    return contract.get_available_credit(addr(who))


def finding_of(contract, publisher, spec, bounty_id=BOUNTY_ID):
    return contract.get_finding(addr(publisher), bounty_id, slot_of(spec, publisher, bounty_id))


def bounty_of(contract, publisher, bounty_id=BOUNTY_ID):
    return contract.get_bounty(addr(publisher), bounty_id)


def run_to_review(contract, direct_vm, publisher, specs, bounty_id=BOUNTY_ID, **bounty_kwargs):
    """Open, commit and reveal every spec, serve the evidence, then enter the review window."""
    open_bounty(contract, direct_vm, publisher, bounty_id, **bounty_kwargs)
    direct_vm.warp(at(NOW_TS + 60))
    for spec in specs:
        commit(contract, direct_vm, publisher, spec, bounty_id)
    direct_vm.warp(at(COMMIT_BY))
    for spec in specs:
        reveal(contract, direct_vm, publisher, spec, bounty_id)
    serve(direct_vm, specs)
    direct_vm.warp(at(REVEAL_BY))


def review_all(contract, direct_vm, publisher, specs, bounty_id=BOUNTY_ID):
    for spec in specs:
        review(contract, direct_vm, publisher, spec, bounty_id=bounty_id)


def check_books(contract, addresses, bounties=()):
    """Assert the whole ledger is internally consistent.

    addresses: every account that may hold credit or a bond.
    bounties: (publisher, bounty_id) pairs whose escrow may still be locked.
    """
    accounting = contract.get_accounting()
    held = sum(credit(contract, who) for who in addresses)
    assert held == accounting["total_credits"], "per-account credits must sum to the aggregate"
    budgets = 0
    bonds = 0
    for publisher, bounty_id in bounties:
        bounty = bounty_of(contract, publisher, bounty_id)
        if bounty["status"] == "open":
            budgets += bounty["budget_wei"]
        for finding in contract.get_findings(addr(publisher), bounty_id)["findings"]:
            bonds += finding["bond_wei"]
    assert budgets == accounting["total_locked_budgets"], "locked budgets must match open bounties"
    assert bonds == accounting["total_locked_bonds"], "locked bonds must match unreleased findings"
    assert accounting["accounted_liabilities"] == (
        accounting["total_credits"] + accounting["total_locked_budgets"]
        + accounting["total_locked_bonds"]
    )
    return accounting


def set_web(direct_vm, url, response):
    """Replace (or add) the mocked response for exactly one URL."""
    direct_vm.mock_web(re.escape(url), response)


def capture_prompts(direct_vm, answer="upheld"):
    """Route every model call to a recorder instead of the pattern mocks."""
    prompts = []
    value = verdict(answer) if isinstance(answer, str) else answer

    def handler(data):
        prompts.append(data.get("prompt", ""))
        return {"ok": json.dumps(value)}

    direct_vm._llm_mocks.clear()
    direct_vm._live_llm_handler = handler
    return prompts
