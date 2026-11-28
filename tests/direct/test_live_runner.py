"""Run the whole live end-to-end scenario against the real contract code in Direct Mode.

The live script cannot reach Studionet from a test, so a simulated chain drives the same
Scenario class against the contract, with the web and the model mocked as an ideal
validator set would answer. That catches scenario bugs (wrong arguments, wrong expected
numbers, wrong ordering) before anyone spends a Codespace hour on them.
"""

import hashlib
import json
import re

import pytest

from scripts import live_e2e
from tests.direct.support import CONTRACT, NOW, NOW_TS, RUNNER, at, llm_pattern

BASE_URL = "https://fixtures.example.org/live/"
# Studio v0.123 charges every transaction a fee out of the sender's own native balance. The default is
# well below the smallest payout; test_a_fee_larger_than_every_payout_still_passes uses a huge one.
SIM_FEE = 2 * 10**13


class SimulatedChain(live_e2e.Chain):
    def __init__(self, contract, direct_vm, state, fee=SIM_FEE, deliver_payouts=True):
        self.contract = contract
        self.fee = fee
        self.deliver_payouts = deliver_payouts
        self.fees_paid = {}
        self.vm = direct_vm
        self.clock = NOW_TS
        self.balances = {}
        self.contract_balance = 0
        self.names = ["funder"] + live_e2e.FINDERS + ["stranger"]
        self.raw = {n: hashlib.sha256(f"sim-{n}".encode()).digest()[:20] for n in self.names}
        self.calls = []

    def now(self):
        return self.clock

    def _warp(self):
        self.vm.warp(at(self.clock))

    def sleep_until(self, timestamp, reason):
        if timestamp > self.clock:
            self.clock = timestamp
            self._warp()

    def address(self, name):
        return "0x" + self.raw[name].hex()

    def balance(self, name_or_address):
        return self.balances.get(name_or_address, 0)

    def fund(self, name, wei):
        self.balances[name] = self.balances.get(name, 0) + wei
        return True

    def write(self, name, function, args, value=0, label="", expect="success"):
        record = live_e2e.TxRecord(label or function, self.address(name), function, expected=expect)
        record.tx_hash = "0x" + hashlib.sha256(f"{len(self.calls)}{label}".encode()).hexdigest()
        self.clock += 15
        self._warp()
        self.calls.append((name, function))
        self.vm.sender = self.raw[name]
        self.vm.value = value
        # The fee is taken at submission and is not returned, whether the call succeeds or reverts.
        assert self.balances.get(name, 0) >= self.fee + value, f"{name} cannot afford the fee and {value}"
        self.balances[name] -= self.fee
        self.fees_paid[name] = self.fees_paid.get(name, 0) + self.fee
        record.fee_wei = self.fee
        if value:
            self.balances[name] -= value
        owed = self.contract.get_available_credit(self.address(name)) if function == "withdraw_credit" else 0
        try:
            getattr(self.contract, function)(*args)
        except Exception as error:  # noqa: BLE001
            assert type(error).__name__ == "UserError", f"{function} crashed: {error!r}"
            record.result = "FINISHED_WITH_ERROR"
            record.status = "FINALIZED"
            if value:
                self.balances[name] += value
        else:
            record.result = "FINISHED_WITH_RETURN"
            record.status = "FINALIZED"
            self.contract_balance += value
            if function == "withdraw_credit":
                if self.deliver_payouts:
                    self.balances[name] = self.balances.get(name, 0) + owed
                self.contract_balance -= owed
        finally:
            self.vm.value = 0
        return record

    def read(self, function, args):
        result = getattr(self.contract, function)(*args)
        if function == "get_accounting":
            accounted = result["accounted_liabilities"]
            result = dict(result, contract_balance=self.contract_balance,
                          solvent=self.contract_balance >= accounted,
                          unallocated_balance=max(0, self.contract_balance - accounted),
                          balance_shortfall=max(0, accounted - self.contract_balance))
        return result


def _mock_world(direct_vm):
    raw = (live_e2e.FIXTURE_DIR / live_e2e.SUBJECT_FILE).read_bytes()
    direct_vm.mock_web(re.escape(BASE_URL + live_e2e.SUBJECT_FILE), {"status": 200, "body": raw})
    direct_vm.mock_web(re.escape(BASE_URL + live_e2e.OFFLINE_FILE), {"status": 404, "body": b"not found"})
    seen = set()
    for name, case in live_e2e.CASES.items():
        if case["expect"] in ("disqualified", "never_reveals"):
            continue
        key = (case["kind"], case["pair"])
        if key in seen:
            continue
        seen.add(key)
        verdict = "upheld" if case["expect"] == "upheld" else "not_upheld"
        direct_vm.mock_llm(llm_pattern(case["kind"], *case["pair"]),
                           json.dumps({"verdict": verdict, "reason": f"simulated validator ({name})"}))


class FastTime:
    """Polling loops in the runner wait for real transfers; here they must not take real minutes."""

    def __init__(self):
        self.now = 1_000_000.0

    def time(self):
        return self.now

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(live_e2e, "STATE_DIR", tmp_path / ".live")
    monkeypatch.setattr(live_e2e, "REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr(live_e2e, "time", FastTime())
    return tmp_path


def _run(direct_vm, direct_deploy, isolated, extra=(), **chain_options):
    direct_vm.warp(NOW)
    contract = direct_deploy(CONTRACT, sdk_version=RUNNER)
    _mock_world(direct_vm)
    holder = {}

    def factory(state):
        chain = SimulatedChain(contract, direct_vm, state, **chain_options)
        holder["chain"] = chain
        return chain

    code = live_e2e.main(["--fixture-base-url", BASE_URL, "--skip-fixture-check",
                          "--commit-seconds", "900", "--reveal-seconds", "600", *extra],
                         chain_factory=factory)
    reports = sorted((isolated / "reports").glob("live-e2e-*.json"))
    assert reports, "the runner must always write a report"
    return code, json.loads(reports[-1].read_text()), holder["chain"], contract


def test_the_whole_live_scenario_passes_against_the_real_contract(direct_vm, direct_deploy, isolated):
    code, report, chain, contract = _run(direct_vm, direct_deploy, isolated)
    failures = [c for c in report["checks"] if not c["passed"]]
    assert failures == [], "\n".join(f"{c['section']}: {c['name']} expected {c['expected']} saw {c['observed']}"
                                     for c in failures)
    assert code == 0 and report["failed"] == 0 and not report["fatal"]
    assert report["passed"] >= 110
    # Every account that earned something got it as real balance, and nothing is left owed.
    earned = {n: chain.balances[n] + chain.fees_paid.get(n, 0) for n in ("alpha", "echo")}
    assert earned["alpha"] > earned["echo"] > 0
    final = contract.get_accounting()
    assert final["accounted_liabilities"] == 0
    markdown = sorted((isolated / "reports").glob("live-e2e-*.md"))[-1].read_text()
    assert "Errata live end-to-end report - PASS" in markdown and "## Validator findings" in markdown


def test_the_scenario_covers_every_documented_outcome(direct_vm, direct_deploy, isolated):
    _, report, _, _ = _run(direct_vm, direct_deploy, isolated)
    names = " | ".join(c["name"] for c in report["checks"])
    for needle in ("alpha screened as upheld", "echo screened as not_upheld",
                   "foxtrot disqualified", "india (dead primary URL)", "duplicate of the first finding",
                   "hotel outcome is unrevealed_bond_forfeited", "ledger conserves",
                   "a decisive verdict cannot be re-rolled", "reveal with a wrong salt",
                   "publisher cannot file on its own bounty", "standing preview",
                   "india sealed three findings in bounty B from one account",
                   "a fourth finding from one account exceeds the per-account cap",
                   "the same commitment cannot be sealed twice",
                   "a finder cannot reveal someone else's sealed finding",
                   "the projected errata sheet", "the settled errata sheet of A",
                   "india's extra findings ended as unrevealed_bond_forfeited"):
        assert needle in names, needle


def test_a_model_that_disagrees_with_the_fixture_is_reported_as_a_failed_check(
    direct_vm, direct_deploy, isolated
):
    direct_vm.warp(NOW)
    contract = direct_deploy(CONTRACT, sdk_version=RUNNER)
    _mock_world(direct_vm)
    # The simulated model upholds the decoy as well: the run must FAIL loudly, not pass.
    direct_vm._llm_mocks.clear()
    for case in live_e2e.CASES.values():
        if case["expect"] in ("disqualified", "never_reveals"):
            continue
        direct_vm.mock_llm(llm_pattern(case["kind"], *case["pair"]),
                           json.dumps({"verdict": "upheld", "reason": "too generous"}))
    code = live_e2e.main(["--fixture-base-url", BASE_URL, "--skip-fixture-check",
                          "--commit-seconds", "900", "--reveal-seconds", "600"],
                         chain_factory=lambda state: SimulatedChain(contract, direct_vm, state))
    assert code == 1
    report = json.loads(sorted((isolated / "reports").glob("live-e2e-*.json"))[-1].read_text())
    failed = [c["name"] for c in report["checks"] if not c["passed"]]
    assert any("echo screened as not_upheld" in name for name in failed)


def test_the_fixture_matches_every_planned_quote_and_hashes_stably():
    raw = (live_e2e.FIXTURE_DIR / live_e2e.SUBJECT_FILE).read_bytes()
    fixtures = live_e2e.load_fixtures(BASE_URL, verify=False)
    assert fixtures["hash"] == "0x" + hashlib.sha256(raw).hexdigest()
    assert b"\r" not in raw, "line endings must stay LF so the pinned hash never changes"
    assert live_e2e.EXPECTED_CREDIT["funder"] == (
        (live_e2e.BUDGET_A - sum(live_e2e.REWARDS_A)) + 2 * live_e2e.BOND
        + live_e2e.EXTRA_SEALS * live_e2e.BOND
        + (live_e2e.BUDGET_B - live_e2e.REWARDS_B[2]))


def test_the_non_payable_probe_passes_when_the_value_is_refused(direct_vm, direct_deploy, isolated):
    code, report, chain, contract = _run(direct_vm, direct_deploy, isolated, extra=["--probe-nonpayable"])
    failures = [c for c in report["checks"] if not c["passed"]]
    assert failures == [] and code == 0
    names = [c["name"] for c in report["checks"]]
    assert any("value attached to a non-payable method is refused" in n for n in names)
    assert any("the finding behind the refused call is untouched" in n for n in names)
    assert contract.get_accounting()["accounted_liabilities"] == 0


def test_the_probe_is_off_by_default(direct_vm, direct_deploy, isolated):
    _, report, _, _ = _run(direct_vm, direct_deploy, isolated)
    assert not any("non-payable" in c["name"] for c in report["checks"])


# ----------------------------------------------------------------------------- Studio v0.123 fees


def _withdraw_checks(report):
    return [c for c in report["checks"] if "withdraw" in c["name"] or "net of the transaction fee" in c["name"]
            or "ledger credit cleared" in c["name"]]


def test_every_sender_pays_a_fee_and_the_funding_plan_covers_it(direct_vm, direct_deploy, isolated):
    code, report, chain, _ = _run(direct_vm, direct_deploy, isolated)
    assert code == 0 and report["failed"] == 0
    # The stranger only sends calls that revert or finalize, yet it pays a fee on every one of them.
    assert chain.fees_paid.get("stranger", 0) > 0
    needs = live_e2e.Scenario(chain, live_e2e.Report(), {}, {"bounty_a": "A", "bounty_b": "B", "salts": {}},
                              900, 600, None).funding_needs()
    assert set(needs) == {"funder", "stranger", *live_e2e.FINDERS}
    assert all(wei >= live_e2e.TX_FEE_BUFFER for wei in needs.values())
    for name, paid in chain.fees_paid.items():
        assert paid < needs[name], f"{name} would run out of GEN for fees"


def test_withdrawals_are_verified_net_of_the_transaction_fee(direct_vm, direct_deploy, isolated):
    _, report, chain, _ = _run(direct_vm, direct_deploy, isolated)
    names = [c["name"] for c in report["checks"]]
    assert any("withdraw_credit transaction succeeded" in n for n in names)
    assert any("balance rose by" in n and "net of the transaction fee" in n for n in names)
    assert not any(n.endswith("wei") and " received " in n for n in names), "the exact-gain check is gone"
    assert all(c["passed"] for c in _withdraw_checks(report))


def test_a_fee_larger_than_every_payout_still_passes(direct_vm, direct_deploy, isolated):
    # Every withdrawing account ends up with LESS than it started with, which the old exact-gain check
    # (and a plain "balance went up" check) would both have rejected.
    code, report, chain, _ = _run(direct_vm, direct_deploy, isolated, fee=10**16)
    failures = [c for c in report["checks"] if not c["passed"]]
    assert failures == [] and code == 0


def test_a_withdrawal_that_never_arrives_is_reported_as_a_failed_check(direct_vm, direct_deploy, isolated):
    code, report, chain, _ = _run(direct_vm, direct_deploy, isolated, deliver_payouts=False)
    assert code == 1
    failed = [c["name"] for c in report["checks"] if not c["passed"]]
    assert any("net of the transaction fee" in name for name in failed), failed
    # The ledger and the transaction result are still fine: only the missing balance is flagged.
    assert not any("withdraw_credit transaction succeeded" in name for name in failed)
    assert not any("ledger credit cleared" in name for name in failed)


class StubClient:
    """Stands in for the genlayer-py 0.19.0 client: records what write/deploy were given."""

    def __init__(self, simulation_works=True):
        self.simulation_works = simulation_works
        self.calls = []

    def estimate_transaction_fees_for_write(self, **kwargs):
        self.calls.append(("simulate", kwargs))
        if not self.simulation_works:
            raise RuntimeError("the write simulation reverted")
        return {"feeValue": 7_000, "fee_value": 7_000}

    def estimate_transaction_fees(self, options=None):
        self.calls.append(("generic", {}))
        return {"feeValue": 3_000, "fee_value": 3_000}

    def write_contract(self, **kwargs):
        self.calls.append(("write", kwargs))
        return "0x" + "ab" * 32

    def deploy_contract(self, **kwargs):
        self.calls.append(("deploy", kwargs))
        return "0x" + "cd" * 32


@pytest.fixture
def genlayer_chain(monkeypatch):
    pytest.importorskip("genlayer_py")
    chain = live_e2e.GenLayerChain("0x" + "11" * 20, {"keys": {}}, "http://localhost:1", 60)
    monkeypatch.setattr(chain, "_throttle", lambda: None)
    monkeypatch.setattr(chain, "_wait", lambda tx_hash: {"status_name": "FINALIZED", "contract_address": "0x" + "22" * 20})
    monkeypatch.setattr(chain, "_dump_receipt", lambda *args, **kwargs: None)
    monkeypatch.setattr(chain, "_execution_outcome", lambda receipt: ("FINISHED_WITH_RETURN", "stub"))
    return chain


def test_write_attaches_the_simulated_fees(genlayer_chain):
    chain = genlayer_chain
    chain.client = StubClient()
    record = chain.write("alpha", "withdraw_credit", [], value=5, label="withdraw")
    kinds = [kind for kind, _ in chain.client.calls]
    assert kinds == ["simulate", "write"], "the simulated estimate is tried first and is enough"
    simulate = chain.client.calls[0][1]
    assert simulate["address"] == chain.contract and simulate["function_name"] == "withdraw_credit"
    assert simulate["account"] is chain.accounts["alpha"] and simulate["args"] == [] and simulate["value"] == 5
    sent = chain.client.calls[1][1]
    assert sent["fees"] == {"feeValue": 7_000, "fee_value": 7_000} and sent["value"] == 5
    assert record.fee_wei == 7_000 and record.succeeded is True


def test_write_falls_back_to_the_generic_estimate_when_simulation_fails(genlayer_chain):
    chain = genlayer_chain
    chain.client = StubClient(simulation_works=False)
    record = chain.write("stranger", "cancel_bounty", ["B-001"], expect="revert")
    kinds = [kind for kind, _ in chain.client.calls]
    assert kinds == ["simulate", "generic", "write"]
    assert chain.client.calls[2][1]["fees"] == {"feeValue": 3_000, "fee_value": 3_000}
    assert record.fee_wei == 3_000 and record.tx_hash, "an expected-revert call is still submitted with fees"


def test_deploy_attaches_the_generic_fee_estimate(genlayer_chain):
    chain = genlayer_chain
    chain.client = StubClient()
    address = chain.deploy("# contract source")
    assert address == "0x" + "22" * 20
    assert [kind for kind, _ in chain.client.calls] == ["generic", "deploy"]
    assert chain.client.calls[1][1]["fees"] == {"feeValue": 3_000, "fee_value": 3_000}
    assert chain.client.calls[1][1]["account"] is chain.accounts["funder"]
