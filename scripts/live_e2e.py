#!/usr/bin/env python3
"""End-to-end live test of Errata on GenLayer Studionet, with a written report.

Run it from a GitHub Codespace (or any machine with internet access):

    python scripts/live_e2e.py --address 0xYourDeployedErrata

It drives two complete bounties through the real network with real validators and a real
LLM, and reads every result back from the contract (never from receipts):

  Bounty A  (the publisher's document is online)
    alpha    arithmetic defect                         -> upheld, rewarded
    bravo    contradiction next to a hostile note      -> upheld, rewarded (the note tells the
                                                          reviewer to answer not_upheld)
    charlie  definition conflict                       -> upheld, rewarded
    delta    same defect as alpha, committed later     -> upheld, but a duplicate (no prize)
    echo     arithmetic claim on a CORRECT calculation -> not_upheld
    foxtrot  a quote that is not in the document       -> disqualified, bond forfeited
    golf     contradiction claim on consistent text    -> not_upheld
    hotel    commits but never reveals                 -> bond forfeited

  Bounty B  (the publisher's URL is dead; the finder brought a mirror)
    india    definition conflict, hash-pinned mirror   -> upheld, rewarded

Then it finalizes both bounties and withdraws every credit as native GEN. A Markdown and a
JSON report are written to ./reports/.

The fixture under fixtures/live/ must be reachable over public HTTPS because the validators
fetch it. The script derives a commit-pinned raw.githubusercontent.com URL from the current
git checkout, verifies that it serves exactly the local bytes, and refuses to start otherwise.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.errata_tools import check_quotes, clean, new_salt, sha256_hex, subject_text  # noqa: E402
from scripts.errata_tools import commitment as make_commitment  # noqa: E402

CONTRACT_PATH = ROOT / "contracts" / "errata.py"
FIXTURE_DIR = ROOT / "fixtures" / "live"
REPORT_DIR = ROOT / "reports"
STATE_DIR = ROOT / ".live"
EXPLORER_TX = "https://explorer-studio.genlayer.com/tx/"
EXPLORER_ADDRESS = "https://explorer-studio.genlayer.com/address/"
DEFAULT_RPC = "https://studio.genlayer.com/api"
GEN = 10**18
MIN_RPC_GAP_SECONDS = 1.2  # Studionet allows 60 requests per minute per IP

SUBJECT_FILE = "handbook.md"
OFFLINE_FILE = "handbook-offline.md"  # deliberately absent from the repository

ARITH, CONTRA, DEFN = 1, 2, 3

REWARDS_A = (400_000_000_000_000, 500_000_000_000_000, 300_000_000_000_000)
BUDGET_A = 1_500_000_000_000_000        # 0.0015 GEN
REWARDS_B = (0, 0, 400_000_000_000_000)
BUDGET_B = 1_000_000_000_000_000        # 0.001 GEN
BOND = 100_000_000_000_000              # 0.0001 GEN, at most the smallest prize on offer
# india seals its real finding plus this many extra ones that are never revealed. That exercises the
# several-findings-per-account rule on the real chain: the extras are forfeited to the publisher and the
# account's cap of three is probed with one more attempt.
EXTRA_SEALS = 2

PAIR_FEE = ("payable in 4 equal quarterly instalments of 50 dollars each", "The annual fee is 240 dollars")
PAIR_FEE_VARIANT = ("4 equal quarterly instalments of 50 dollars", "annual fee is 240 dollars")
PAIR_HOURS = ("The pool is open every day of the week from 6 am to 9 pm",
              "The pool is closed on Mondays for maintenance")
PAIR_PEAK = ('"Peak hours" means 5 pm to 8 pm on weekdays',
             "peak hours, which run from 4 pm to 7 pm on weekdays")
PAIR_LANES = ("The main pool has 6 lanes and each lane is 25 metres long",
              "so the lap total across all lanes is 150 metres")
PAIR_FAKE = ("payable in 4 equal quarterly instalments of 50 dollars each",
             "The pool is heated to 40 degrees all year round")
PAIR_SAFETY = ("Lifeguards are on duty whenever the pool is open",
               "Running on the pool deck is not permitted")

# name -> (bounty, kind, quotes, note, expected status after screening)
CASES: dict[str, dict] = {
    "alpha": dict(bounty="A", kind=ARITH, pair=PAIR_FEE, expect="upheld",
                  note="Four instalments of 50 dollars add up to 200, not 240."),
    "bravo": dict(bounty="A", kind=CONTRA, pair=PAIR_HOURS, expect="upheld",
                  note="Open every day, yet closed on Mondays."),
    "charlie": dict(bounty="A", kind=DEFN, pair=PAIR_PEAK, expect="upheld", note=""),
    "delta": dict(bounty="A", kind=ARITH, pair=PAIR_FEE_VARIANT, expect="upheld",
                  note="Same instalment arithmetic, quoted more tightly."),
    "echo": dict(bounty="A", kind=ARITH, pair=PAIR_LANES, expect="not_upheld", note="Claims 150 is wrong."),
    "foxtrot": dict(bounty="A", kind=ARITH, pair=PAIR_FAKE, expect="disqualified", note=""),
    "golf": dict(bounty="A", kind=CONTRA, pair=PAIR_SAFETY, expect="not_upheld", note=""),
    "hotel": dict(bounty="A", kind=CONTRA, pair=PAIR_HOURS, expect="never_reveals", note=""),
    "india": dict(bounty="B", kind=DEFN, pair=PAIR_PEAK, expect="upheld", note="Found via a mirror."),
}
FINDERS = list(CASES)
A_ORDER = [name for name in FINDERS if CASES[name]["bounty"] == "A"]
REVEALERS = [name for name in FINDERS if CASES[name]["expect"] != "never_reveals"]

EXPECTED_CREDIT = {
    "alpha": BOND + REWARDS_A[0],
    "bravo": BOND + REWARDS_A[1],
    "charlie": BOND + REWARDS_A[2],
    "delta": BOND,
    "echo": BOND,
    "foxtrot": 0,
    "golf": BOND,
    "hotel": 0,
    "india": BOND + REWARDS_B[2],
    "funder": ((BUDGET_A - sum(REWARDS_A)) + 2 * BOND + EXTRA_SEALS * BOND
               + (BUDGET_B - REWARDS_B[2])),
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(message: str) -> None:
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


# ---------------------------------------------------------------------------- results


@dataclass
class TxRecord:
    label: str
    account: str
    function: str
    tx_hash: str = ""
    status: str = ""
    result: str = ""
    seconds: float = 0.0
    expected: str = "success"
    note: str = ""

    @property
    def link(self) -> str:
        return EXPLORER_TX + self.tx_hash if self.tx_hash else ""

    @property
    def succeeded(self) -> bool | None:
        text = self.result.upper()
        if "FINISHED_WITH_RETURN" in text:
            return True
        if "FINISHED_WITH_ERROR" in text or "ERROR" in text:
            return False
        return None


@dataclass
class Check:
    name: str
    passed: bool
    expected: str
    observed: str
    section: str = ""


@dataclass
class Report:
    started: str = field(default_factory=utc_now)
    finished: str = ""
    environment: dict = field(default_factory=dict)
    checks: list = field(default_factory=list)
    txs: list = field(default_factory=list)
    sections: dict = field(default_factory=dict)
    fatal: str = ""

    def check(self, section: str, name: str, passed: bool, expected: Any, observed: Any) -> bool:
        record = Check(name, bool(passed), str(expected), str(observed), section)
        self.checks.append(record)
        log(("PASS " if passed else "FAIL ") + f"{section}: {name} (expected {expected}, saw {observed})")
        return bool(passed)

    @property
    def all_passed(self) -> bool:
        return not self.fatal and bool(self.checks) and all(c.passed for c in self.checks)


# -------------------------------------------------------------------------- chain access


class ChainError(RuntimeError):
    pass


class Chain:
    """What the scenario needs from a network. Real and simulated chains implement it."""

    def now(self) -> int: ...
    def sleep_until(self, timestamp: int, reason: str) -> None: ...
    def address(self, name: str) -> str: ...
    def write(self, name: str, function: str, args: list, value: int = 0,
              label: str = "", expect: str = "success") -> TxRecord: ...
    def read(self, function: str, args: list) -> Any: ...
    def balance(self, name_or_address: str) -> int: ...
    def fund(self, name: str, wei: int) -> bool: ...


class GenLayerChain(Chain):
    """Studionet through genlayer-py. Only thin, defensive wrappers live here."""

    def __init__(self, contract_address: str, state: dict, rpc_url: str | None, wait_seconds: int):
        try:
            from eth_account import Account
            from genlayer_py import create_account, create_client
            from genlayer_py.chains import studionet
        except ImportError as error:  # pragma: no cover - environment dependent
            raise ChainError(
                "genlayer-py is not installed. Run: python -m pip install -r requirements.txt"
            ) from error
        self._Account = Account
        self._create_account = create_account
        self.contract = contract_address
        self.state = state
        self.rpc_url = rpc_url or self._chain_rpc(studionet)
        self.wait_seconds = wait_seconds
        self._last_rpc = 0.0
        self.accounts: dict[str, Any] = {}
        for name in ["funder"] + FINDERS + ["stranger"]:
            key = state["keys"].get(name)
            if key is None:
                account = create_account()
                state["keys"][name] = self._key_hex(account)
            else:
                account = Account.from_key(key)
            self.accounts[name] = account
        self.client = create_client(chain=studionet, account=self.accounts["funder"])

    @staticmethod
    def _chain_rpc(chain) -> str:
        try:
            return chain.rpc_urls["default"]["http"][0]
        except Exception:  # noqa: BLE001
            return DEFAULT_RPC

    @staticmethod
    def _key_hex(account) -> str:
        key = getattr(account, "key", None) or getattr(account, "_private_key")
        text = key.hex() if hasattr(key, "hex") else str(key)
        return text if text.startswith("0x") else "0x" + text

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last_rpc
        if gap < MIN_RPC_GAP_SECONDS:
            time.sleep(MIN_RPC_GAP_SECONDS - gap)
        self._last_rpc = time.monotonic()

    def _rpc(self, method: str, params: list) -> Any:
        self._throttle()
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        request = urllib.request.Request(
            self.rpc_url, data=body,
            headers={"content-type": "application/json", "accept": "application/json",
                     "user-agent": "errata-live-e2e/0.1 (+python-urllib)"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:300]
            raise ChainError(f"{method}: HTTP {error.code} from {self.rpc_url}: {detail}") from error
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            raise ChainError(f"{method}: cannot reach {self.rpc_url}: {error}") from error
        if "error" in payload:
            raise ChainError(f"{method}: {payload['error']}")
        return payload.get("result")

    def now(self) -> int:
        return int(time.time())

    def sleep_until(self, timestamp: int, reason: str) -> None:
        remaining = timestamp - self.now()
        if remaining > 0:
            log(f"waiting {remaining}s for {reason}")
        while True:
            remaining = timestamp - self.now()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 30))

    def address(self, name: str) -> str:
        return self.accounts[name].address

    def balance(self, name_or_address: str) -> int:
        address = self.address(name_or_address) if name_or_address in self.accounts else name_or_address
        return int(self._rpc("eth_getBalance", [address, "latest"]), 16)

    def fund(self, name: str, wei: int) -> bool:
        address = self.address(name)
        for params in ([address, wei], [address, hex(wei)]):
            try:
                self._rpc("sim_fundAccount", params)
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    # ---- contract calls
    def _wait(self, tx_hash: str):
        interval_ms = 4000
        retries = max(10, int(self.wait_seconds * 1000 / interval_ms))
        try:
            return self.client.wait_for_transaction_receipt(
                transaction_hash=tx_hash, wait_until="finalized", interval=interval_ms,
                retries=retries,
            )
        except TypeError:
            from genlayer_py.types import TransactionStatus
            return self.client.wait_for_transaction_receipt(
                transaction_hash=tx_hash, status=TransactionStatus.FINALIZED,
                interval=interval_ms, retries=retries,
            )

    @staticmethod
    def _get(node: Any, key: str) -> Any:
        return node.get(key) if isinstance(node, dict) else getattr(node, key, None)

    @classmethod
    def _execution_outcome(cls, receipt: Any) -> tuple[str, str]:
        """(FINISHED_WITH_RETURN | FINISHED_WITH_ERROR | "", raw evidence).

        Only the final leader receipt and the top-level execution result are consulted.
        Validator receipts and nested history can contain errors that consensus overruled,
        and the consensus vote (for example MAJORITY_AGREE) never says whether a call reverted.
        """
        leader_text = ""
        consensus = cls._get(receipt, "consensus_data")
        leaders = cls._get(consensus, "leader_receipt") if consensus is not None else None
        if isinstance(leaders, (list, tuple)) and leaders:
            leader_text = str(cls._get(leaders[0], "execution_result") or "").upper()
        top = cls._get(receipt, "tx_execution_result_name")
        top_text = str(getattr(top, "name", top) or "").upper()
        evidence = f"leader={leader_text or '-'}, top={top_text or '-'}"
        if leader_text in ("SUCCESS", "ERROR"):
            return ("FINISHED_WITH_RETURN" if leader_text == "SUCCESS" else "FINISHED_WITH_ERROR"), evidence
        if top_text in ("FINISHED_WITH_RETURN", "FINISHED_WITH_ERROR"):
            return top_text, evidence
        return "", evidence

    def _dump_receipt(self, tx_hash: str, receipt: Any) -> None:
        try:
            folder = STATE_DIR / "receipts"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{tx_hash}.json").write_text(json.dumps(receipt, indent=2, default=str)[:60000])
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _transient(error: Exception) -> bool:
        text = str(error)
        return any(marker in text for marker in ("502", "503", "504", "Bad gateway", "invalid JSON",
                                                 "timed out", "Connection"))

    @staticmethod
    def _field(receipt: Any, *names: str) -> str:
        for name in names:
            value = receipt.get(name) if isinstance(receipt, dict) else getattr(receipt, name, None)
            if value not in (None, ""):
                return str(getattr(value, "name", value))
        return ""

    def write(self, name: str, function: str, args: list, value: int = 0,
              label: str = "", expect: str = "success") -> TxRecord:
        record = TxRecord(label or function, self.address(name), function, expected=expect)
        started = time.monotonic()
        try:
            tx_hash = ""
            for attempt in range(4):
                self._throttle()
                try:
                    tx_hash = self.client.write_contract(
                        address=self.contract, function_name=function, account=self.accounts[name],
                        args=args, value=value,
                    )
                    break
                except Exception as error:  # noqa: BLE001
                    if attempt == 3 or not self._transient(error):
                        raise
                    time.sleep(5 * (attempt + 1))
            record.tx_hash = str(tx_hash)
            receipt = self._wait(record.tx_hash)
            self._dump_receipt(record.tx_hash, receipt)
            record.status = self._field(receipt, "status_name", "statusName", "status")
            record.result, evidence = self._execution_outcome(receipt)
            vote = self._field(receipt, "result_name", "resultName")
            record.note = f"consensus={vote or '?'}; {evidence}"
        except Exception as error:  # noqa: BLE001
            record.note = f"{type(error).__name__}: {error}"
            if expect == "success":
                record.result = record.result or "SUBMISSION_ERROR"
        record.seconds = round(time.monotonic() - started, 1)
        return record

    def read(self, function: str, args: list) -> Any:
        for attempt in range(4):
            self._throttle()
            try:
                return self.client.read_contract(address=self.contract, function_name=function,
                                                 args=args)
            except Exception as error:  # noqa: BLE001
                if attempt == 3 or not self._transient(error):
                    raise
                time.sleep(5 * (attempt + 1))

    # ---- deployment (best effort convenience)
    def deploy(self, code: str) -> str:
        self._throttle()
        tx_hash = str(self.client.deploy_contract(code=code, args=[], account=self.accounts["funder"]))
        receipt = self._wait(tx_hash)
        found = _find_address(receipt)
        if not found:
            raise ChainError(
                f"Deployment {tx_hash} finalized but the contract address could not be read "
                "from the receipt. Copy it from the Studionet explorer and pass --address."
            )
        return found


def _find_address(node: Any, depth: int = 0) -> str:
    if depth > 6:
        return ""
    keys = ("contract_address", "contractAddress", "created_address", "recipient", "to_address")
    if isinstance(node, dict):
        for key in keys:
            value = node.get(key)
            if isinstance(value, str) and value.startswith("0x") and len(value) == 42 \
                    and int(value, 16) != 0:
                return value
        for value in node.values():
            found = _find_address(value, depth + 1)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_address(value, depth + 1)
            if found:
                return found
    return ""


# --------------------------------------------------------------------------- fixtures


def git_output(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:  # noqa: BLE001
        return ""


def fixture_base_url(override: str | None) -> str:
    if override:
        return override.rstrip("/") + "/"
    repository = os.environ.get("GITHUB_REPOSITORY") or _repo_from_remote()
    commit = git_output("rev-parse", "HEAD")
    if not repository or not commit:
        raise ChainError(
            "Cannot derive the fixture URL. Set --fixture-base-url to a public, commit-pinned "
            "directory URL that serves the files in fixtures/live/."
        )
    return f"https://raw.githubusercontent.com/{repository}/{commit}/fixtures/live/"


def _repo_from_remote() -> str:
    remote = git_output("config", "--get", "remote.origin.url")
    for prefix in ("https://github.com/", "git@github.com:"):
        if remote.startswith(prefix):
            return remote[len(prefix):].removesuffix(".git")
    return ""


def load_fixtures(base_url: str, verify: bool) -> dict[str, Any]:
    raw = (FIXTURE_DIR / SUBJECT_FILE).read_bytes()
    fixtures = {
        "bytes": raw,
        "hash": sha256_hex(raw),
        "url": base_url + SUBJECT_FILE,
        "offline_url": base_url + OFFLINE_FILE,
        "text": subject_text(raw),
    }
    problems = []
    for name, (quote_a, quote_b) in {n: c["pair"] for n, c in CASES.items()}.items():
        found = check_quotes(raw, quote_a, quote_b)
        expected_bad = CASES[name]["expect"] == "disqualified"
        if found and not expected_bad:
            problems.append(f"{name}: {'; '.join(found)}")
        if not found and expected_bad:
            problems.append(f"{name}: expected unusable quotes but they are valid")
    if problems:
        raise ChainError("The scenario quotes do not match fixtures/live/" + SUBJECT_FILE
                         + ":\n  " + "\n  ".join(problems))
    if verify:
        try:
            with urllib.request.urlopen(fixtures["url"], timeout=30) as response:
                served = response.read()
        except (urllib.error.URLError, TimeoutError) as error:
            raise ChainError(
                "Fixture check failed. Commit and push fixtures/live/ to a PUBLIC repository "
                f"first (validators must be able to fetch it):\n  {fixtures['url']} -> {error}"
            ) from error
        if served != raw:
            raise ChainError(f"{fixtures['url']} serves different bytes than the local file")
        try:
            with urllib.request.urlopen(fixtures["offline_url"], timeout=30) as response:
                offline_status = response.status
        except urllib.error.HTTPError as error:
            offline_status = error.code
        except (urllib.error.URLError, TimeoutError):
            offline_status = 0
        if 200 <= offline_status < 300:
            raise ChainError(f"{fixtures['offline_url']} must NOT exist: bounty B relies on a dead URL")
    return fixtures


# ---------------------------------------------------------------------------- scenario


class Scenario:
    def __init__(self, chain: Chain, report: Report, fixtures: dict, state: dict,
                 commit_seconds: int, reveal_seconds: int, state_path: Path | None,
                 probe_nonpayable: bool = False):
        self.probe_nonpayable = probe_nonpayable
        self.chain = chain
        self.report = report
        self.fixtures = fixtures
        self.state = state
        self.commit_seconds = commit_seconds
        self.reveal_seconds = reveal_seconds
        self.state_path = state_path
        self.funder = chain.address("funder")
        self.bounty_ids = {"A": state["bounty_a"], "B": state["bounty_b"]}
        self.salts = state["salts"]

    # ---- helpers
    def save(self) -> None:
        if self.state_path:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self.state, indent=2))

    def tx(self, name: str, function: str, args: list, *, value: int = 0, label: str = "",
           expect: str = "success", section: str = "") -> TxRecord:
        record = self.chain.write(name, function, args, value=value, label=label or function,
                                  expect=expect)
        self.report.txs.append(record)
        outcome = record.succeeded
        if expect == "success":
            log(f"tx {record.label}: {record.result or 'UNKNOWN'} [{record.note}] "
                f"({record.seconds}s)")
            if outcome is False or record.result == "SUBMISSION_ERROR":
                self.report.check(section or "transactions", f"{record.label} executed", False,
                                  "FINISHED_WITH_RETURN", record.result or record.note)
        elif expect == "revert":
            log(f"tx {record.label}: expected revert, saw {record.result or 'UNKNOWN'} [{record.note}]")
        else:
            log(f"tx {record.label}: {record.result or 'UNKNOWN'} [{record.note}] ({record.seconds}s; "
                "a retry is allowed, the state read back decides)")
        return record

    def view(self, function: str, args: list | None = None):
        return self.chain.read(function, args or [])

    def bounty(self, which: str) -> dict:
        return self.view("get_bounty", [self.funder, self.bounty_ids[which]])

    def index_of(self, name: str) -> int:
        """Where the chain put this finder's first sealed finding in its bounty (commit order)."""
        bounty_id = self.bounty_ids[CASES[name]["bounty"]]
        address = self.chain.address(name).lower()
        for item in self.view("get_findings", [self.funder, bounty_id])["findings"]:
            if str(item["finder"]).lower() == address:
                return int(item["index"])
        raise ChainError(f"{name} has no sealed finding in bounty {CASES[name]['bounty']} yet")

    def extra_salt(self, name: str, number: int) -> str:
        return hashlib.sha256(f"{self.salts[name]}-extra-{number}".encode()).hexdigest()[:40]

    def finding(self, name: str) -> dict:
        return self.view("get_finding", [self.funder, self.bounty_ids[CASES[name]["bounty"]],
                                         self.index_of(name)])

    def credit(self, name: str) -> int:
        return int(self.view("get_available_credit", [self.chain.address(name)]))

    def mirror_of(self, name: str) -> str:
        return self.fixtures["url"] if name == "india" else ""

    def commitment_for(self, name: str, **override) -> str:
        case = CASES[name]
        quote_a, quote_b = case["pair"]
        return make_commitment(
            self.funder, self.bounty_ids[case["bounty"]], self.chain.address(name),
            override.get("kind", case["kind"]), override.get("quote_a", quote_a),
            override.get("quote_b", quote_b), case["note"], self.mirror_of(name),
            override.get("salt", self.salts[name]),
        )

    def reveal_args(self, name: str, **override) -> list:
        case = CASES[name]
        quote_a, quote_b = case["pair"]
        return [
            self.funder, self.bounty_ids[case["bounty"]], self.index_of(name),
            override.get("kind", case["kind"]),
            override.get("quote_a", quote_a), override.get("quote_b", quote_b), case["note"],
            self.mirror_of(name), override.get("salt", self.salts[name]),
        ]

    def revert_check(self, section: str, name: str, who: str, function: str, args: list,
                     value: int = 0) -> None:
        """A call that must not change anything. State is compared before and after."""
        before = json.dumps(self.view("get_accounting"), sort_keys=True)
        record = self.tx(who, function, args, value=value, label=name, expect="revert",
                         section=section)
        after = json.dumps(self.view("get_accounting"), sort_keys=True)
        rejected = record.succeeded is False or record.result == "SUBMISSION_ERROR"
        self.report.check(section, f"{name}: rejected", rejected,
                          "FINISHED_WITH_ERROR (or refused at submission)",
                          record.result or f"UNKNOWN ({record.note})")
        self.report.check(section, f"{name}: books unchanged", before == after, "unchanged",
                          "unchanged" if before == after else "CHANGED")

    def require_time_before(self, key: str, margin: int, doing: str) -> None:
        """Stop with a clear message instead of racing a deadline that is about to pass."""
        deadline = int(self.state.get(key, 0))
        if deadline and self.chain.now() > deadline - margin:
            raise ChainError(
                f"The {key} deadline of this bounty is too close or already past while {doing}. "
                "Do not --resume an old run: start a fresh one (omit --resume) and consider "
                "raising --commit-seconds / --reveal-seconds."
            )

    # ---- phases
    def funding_needs(self) -> dict:
        needs = {"funder": BUDGET_A + BUDGET_B + 2 * BOND}
        for name in FINDERS:
            needs[name] = BOND + 10**14
        needs["india"] += EXTRA_SEALS * BOND
        return needs

    def print_funding_plan(self) -> None:
        log("test accounts (kept in .live/accounts.json, which is git-ignored):")
        for name, wei in self.funding_needs().items():
            log(f"  {name:8s} {self.chain.address(name)}  needs {wei / GEN:.5f} GEN")

    def baseline(self) -> dict:
        """Contract-wide accounting before this run touched anything (other runs share it)."""
        if "baseline" not in self.state:
            accounting = self.view("get_accounting")
            self.state["baseline"] = {k: int(accounting[k]) for k in
                                      ("total_credits", "total_locked_budgets", "total_locked_bonds",
                                       "accounted_liabilities")}
            self.save()
        return self.state["baseline"]

    def delta(self, accounting: dict, key: str) -> int:
        return int(accounting[key]) - int(self.state.get("baseline", {}).get(key, 0))

    def phase_funding(self) -> None:
        section = "1. Funding"
        self.baseline()
        self.print_funding_plan()
        needs = self.funding_needs()
        for name, wei in needs.items():
            if self.chain.balance(name) >= wei:
                continue
            self.chain.fund(name, wei * 2)
        shortfalls = {n: w for n, w in needs.items() if self.chain.balance(n) < w}
        for name, wei in needs.items():
            have = self.chain.balance(name)
            self.report.check(section, f"{name} holds enough GEN ({wei / GEN:.5f})",
                              have >= wei, f">= {wei}", have)
        if shortfalls:
            lines = "\n".join(f"  {self.chain.address(n)}  needs {w / GEN:.5f} GEN"
                              for n, w in shortfalls.items())
            raise ChainError(
                "Automatic funding is not available on this network or was refused. Send GEN to:\n"
                f"{lines}\nthen re-run with --resume {self.state['run_id']}"
            )

    def phase_open(self) -> None:
        section = "2. Open bounties"
        if self.state.get("opened"):
            return
        self.tx("funder", "deposit_credit", [], value=BUDGET_A + BUDGET_B,
                label="publisher deposits both budgets", section=section)
        now = self.chain.now()
        self.state["commit_by"] = now + self.commit_seconds
        self.state["reveal_by"] = now + self.commit_seconds + self.reveal_seconds
        self.tx("funder", "open_bounty", [
            self.bounty_ids["A"], "Harborview handbook errata bounty",
            self.fixtures["url"], self.fixtures["hash"], *REWARDS_A, BUDGET_A, BOND, 8,
            self.state["commit_by"], self.state["reveal_by"],
        ], label="open bounty A (document online)", section=section)
        self.tx("funder", "open_bounty", [
            self.bounty_ids["B"], "Harborview handbook errata bounty (dead primary URL)",
            self.fixtures["offline_url"], self.fixtures["hash"], *REWARDS_B, BUDGET_B, BOND, 4,
            self.state["commit_by"], self.state["reveal_by"],
        ], label="open bounty B (primary URL is dead)", section=section)
        self.state["opened"] = True
        self.save()
        a, b = self.bounty("A"), self.bounty("B")
        self.report.check(section, "bounty A is open", a["status"] == "open", "open", a["status"])
        self.report.check(section, "bounty B is open", b["status"] == "open", "open", b["status"])
        self.report.check(section, "budgets escrowed",
                          (a["budget_wei"], b["budget_wei"]) == (BUDGET_A, BUDGET_B),
                          (BUDGET_A, BUDGET_B), (a["budget_wei"], b["budget_wei"]))
        self.report.check(section, "publisher credit fully allocated", self.credit("funder") == 0, 0,
                          self.credit("funder"))
        self.report.check(section, "subject hash is pinned on chain",
                          a["subject_hash"] == self.fixtures["hash"], self.fixtures["hash"],
                          a["subject_hash"])
        self.report.sections[section] = {"A": a, "B": b}

    def phase_commit(self) -> None:
        section = "3. Sealed commitments"
        if self.state.get("committed"):
            return
        for name in FINDERS:
            self.require_time_before("commit_by", 90, "committing findings")
            self.tx(name, "deposit_credit", [], value=BOND, label=f"{name} deposits bond",
                    section=section)
            self.require_time_before("commit_by", 45, "committing findings")
            self.tx(name, "commit_finding",
                    [self.funder, self.bounty_ids[CASES[name]["bounty"]], self.commitment_for(name)],
                    label=f"{name} commits a sealed finding", section=section)
        for number in range(1, EXTRA_SEALS + 1):
            self.require_time_before("commit_by", 90, "sealing extra findings")
            self.tx("india", "deposit_credit", [], value=BOND, label=f"india deposits bond for extra #{number}",
                    section=section)
            self.require_time_before("commit_by", 45, "sealing extra findings")
            self.tx("india", "commit_finding",
                    [self.funder, self.bounty_ids["B"],
                     self.commitment_for("india", salt=self.extra_salt("india", number))],
                    label=f"india seals extra finding #{number} (never revealed)", section=section)
        self.state["committed"] = True
        self.save()
        a, b = self.bounty("A"), self.bounty("B")
        self.report.check(section, "eight sealed findings in bounty A", a["finding_count"] == 8, 8,
                          a["finding_count"])
        self.report.check(section, "india sealed three findings in bounty B from one account",
                          b["finding_count"] == 1 + EXTRA_SEALS, 1 + EXTRA_SEALS, b["finding_count"])
        india_rows = self.view("get_findings", [self.funder, self.bounty_ids["B"]])["findings"]
        self.report.check(section, "the chain numbers india's findings 0, 1, 2 in commit order",
                          [int(row["index"]) for row in india_rows] == list(range(1 + EXTRA_SEALS))
                          and {str(row["finder"]).lower() for row in india_rows}
                          == {self.chain.address("india").lower()},
                          "indexes 0..2, one finder", [(row["index"], str(row["finder"])[:8]) for row in india_rows])
        sealed = self.finding("bravo")
        self.report.check(section, "quotes stay hidden while sealed",
                          sealed["quote_a"] == "" and sealed["kind"] == 0 and sealed["status"] == "committed",
                          "empty quote, kind 0, committed",
                          f"{sealed['quote_a']!r}, kind {sealed['kind']}, {sealed['status']}")
        locked_bonds = self.delta(self.view("get_accounting"), "total_locked_bonds")
        self.report.check(section, "bonds locked (change since run start)",
                          locked_bonds == (9 + EXTRA_SEALS) * BOND, (9 + EXTRA_SEALS) * BOND,
                          locked_bonds)
        # Adversarial checks that must be rejected on-chain.
        self.revert_check(section, "publisher cannot file on its own bounty", "funder",
                          "commit_finding",
                          [self.funder, self.bounty_ids["A"], self.commitment_for("alpha")])
        self.revert_check(section, "the same commitment cannot be sealed twice", "alpha",
                          "commit_finding",
                          [self.funder, self.bounty_ids["A"], self.commitment_for("alpha")])
        self.revert_check(section, "a fourth finding from one account exceeds the per-account cap",
                          "india", "commit_finding",
                          [self.funder, self.bounty_ids["B"],
                           self.commitment_for("india", salt=self.extra_salt("india", EXTRA_SEALS + 1))])
        self.revert_check(section, "early reveal before the sealing deadline", "alpha",
                          "reveal_finding", self.reveal_args("alpha"))
        self.revert_check(section, "stranger cannot request a review", "stranger", "review_finding",
                          [self.funder, self.bounty_ids["A"], self.index_of("alpha")])
        self.revert_check(section, "a finder cannot reveal someone else's sealed finding", "bravo",
                          "reveal_finding", self.reveal_args("alpha"))
        if self.probe_nonpayable:
            extra = EXTRA_SEALS  # index of india's last extra finding in bounty B
            self.revert_check(section, "value attached to a non-payable method is refused", "india",
                              "withdraw_finding", [self.funder, self.bounty_ids["B"], extra], value=1)
            still = self.view("get_finding", [self.funder, self.bounty_ids["B"], extra])
            self.report.check(section, "the finding behind the refused call is untouched",
                              still["status"] == "committed", "committed", still["status"])

    def phase_reveal(self) -> None:
        section = "4. Reveal"
        if self.state.get("revealed"):
            return
        self.chain.sleep_until(self.state["commit_by"] + 5, "the sealing window to close")
        self.revert_check(section, "late commitment after the deadline", "stranger", "commit_finding",
                          [self.funder, self.bounty_ids["A"], self.commitment_for("alpha")])
        self.revert_check(section, "reveal with a wrong salt", "bravo", "reveal_finding",
                          self.reveal_args("bravo", salt=self.salts["bravo"][::-1]))
        self.revert_check(section, "reveal with a changed quote", "bravo", "reveal_finding",
                          self.reveal_args("bravo", quote_b=CASES["bravo"]["pair"][1] + " in winter"))
        self.revert_check(section, "reveal with a changed defect kind", "bravo", "reveal_finding",
                          self.reveal_args("bravo", kind=ARITH))
        for name in REVEALERS:
            self.require_time_before("reveal_by", 45, "revealing findings")
            self.tx(name, "reveal_finding", self.reveal_args(name), label=f"{name} reveals",
                    section=section)
        self.state["revealed"] = True
        self.save()
        for name in REVEALERS:
            state = self.finding(name)
            quote_a, quote_b = CASES[name]["pair"]
            self.report.check(
                section, f"{name} revealed exactly what was sealed",
                state["status"] == "revealed" and state["quote_a"] == clean(quote_a)
                and state["quote_b"] == clean(quote_b) and state["kind"] == CASES[name]["kind"],
                f"revealed, kind {CASES[name]['kind']}", f"{state['status']}, kind {state['kind']}",
            )
        hotel = self.finding("hotel")
        self.report.check(section, "hotel (never reveals) stays sealed", hotel["status"] == "committed",
                          "committed", hotel["status"])

    def phase_screening(self) -> None:
        section = "5. Validator screening"
        if self.state.get("screened"):
            return
        self.chain.sleep_until(self.state["reveal_by"] + 5, "the reveal window to close")
        details = {}
        for name in REVEALERS:
            attempts = 0
            while attempts < 6:
                caller = "funder" if attempts % 2 == 0 else name
                # A try may legitimately end without consensus on a real validator set. The
                # state read back below, not the single transaction, decides pass or fail.
                self.tx(caller, "review_finding",
                        [self.funder, self.bounty_ids[CASES[name]["bounty"]], self.index_of(name)],
                        label=f"screen {name} (try {attempts + 1})", expect="retryable",
                        section=section)
                state = self.finding(name)
                attempts += 1
                if state["status"] != "revealed":
                    break
                self.chain.sleep_until(max(state["retry_at"], self.chain.now()) + 2,
                                       f"{name} review cooldown")
            details[name] = state
            expected = CASES[name]["expect"]
            self.report.check(
                section, f"{name} screened as {expected}", state["status"] == expected, expected,
                f"{state['status']} (attempts {state['attempts']}, calls {state['review_calls']}, "
                f"reason={state['reason'] or '-'}, rationale={state['rationale'] or '-'})",
            )
        text = self.fixtures["text"]
        for name in ("alpha", "bravo", "charlie", "delta", "echo", "golf", "india"):
            quote_a, quote_b = (clean(q) for q in CASES[name]["pair"])
            want = [text.find(quote_a), text.find(quote_a) + len(quote_a),
                    text.find(quote_b), text.find(quote_b) + len(quote_b)]
            got = [details[name]["a_start"], details[name]["a_end"], details[name]["b_start"],
                   details[name]["b_end"]]
            self.report.check(section, f"{name}: chain located both quotes where the client did",
                              got == want, want, got)
        self.report.check(section, "foxtrot disqualified for a quote that is not in the document",
                          details["foxtrot"]["reason"] == "evidence:quote_b_not_found",
                          "evidence:quote_b_not_found", details["foxtrot"]["reason"])
        self.report.check(section, "india (dead primary URL) was resolved through its mirror",
                          details["india"]["status"] == "upheld" and details["india"]["review_calls"] <= 2,
                          "upheld via mirror", f"{details['india']['status']} after "
                          f"{details['india']['review_calls']} charged call(s)")
        self.revert_check(section, "a decisive verdict cannot be re-rolled", "funder", "review_finding",
                          [self.funder, self.bounty_ids["A"], self.index_of("alpha")])
        standing = self.view("get_standing", [self.funder, self.bounty_ids["A"]])
        outcomes = [entry["projected_outcome"] for entry in standing["entries"]]
        want_outcomes = ["rewarded", "rewarded", "rewarded", "duplicate_bond_refunded",
                         "not_upheld_bond_refunded", "disqualified_bond_forfeited",
                         "not_upheld_bond_refunded", "unrevealed_bond_forfeited"]
        self.report.check(section, "the standing preview shows the expected outcomes",
                          outcomes == want_outcomes, want_outcomes, outcomes)
        self.report.check(section, "the preview pays three distinct defects",
                          standing["projected_paid"] == sum(REWARDS_A), sum(REWARDS_A),
                          standing["projected_paid"])
        sheet = self.view("get_errata", [self.funder, self.bounty_ids["A"]])
        self.report.check(section, "the projected errata sheet lists the three distinct upheld defects",
                          sheet["finalized"] is False and [e["index"] for e in sheet["entries"]] == [0, 1, 2]
                          and [e["reward_wei"] for e in sheet["entries"]] == list(REWARDS_A),
                          "indexes 0,1,2 paid as offered",
                          [(e["index"], e["reward_wei"]) for e in sheet["entries"]])
        self.report.sections[section] = {"findings": details, "standing": standing, "errata": sheet}
        self.state["screened"] = True
        self.save()

    def phase_finalize(self) -> None:
        section = "6. Finalization"
        if not self.state.get("finalized"):
            self.revert_check(section, "a stranger cannot cancel a bounty that has findings",
                              "stranger", "cancel_bounty", [self.bounty_ids["A"]])
            for which in ("A", "B"):
                self.tx("stranger", "finalize_bounty", [self.funder, self.bounty_ids[which]],
                        label=f"stranger finalizes bounty {which} (permissionless)", section=section)
            self.state["finalized"] = True
            self.save()
            self.revert_check(section, "finalizing a second time", "funder", "finalize_bounty",
                              [self.funder, self.bounty_ids["A"]])
        a, b = self.bounty("A"), self.bounty("B")
        self.report.check(section, "both bounties finalized",
                          (a["status"], b["status"]) == ("finalized", "finalized"),
                          "finalized, finalized", (a["status"], b["status"]))
        self.report.check(section, "A paid three distinct defects", a["rewarded_count"] == 3
                          and a["paid_wei"] == sum(REWARDS_A), f"3 rewards, {sum(REWARDS_A)} wei",
                          f"{a['rewarded_count']} rewards, {a['paid_wei']} wei")
        self.report.check(section, "A returned the unspent budget",
                          a["returned_wei"] == BUDGET_A - sum(REWARDS_A), BUDGET_A - sum(REWARDS_A),
                          a["returned_wei"])
        self.report.check(section, "A forfeited exactly two bonds (foxtrot, hotel)",
                          a["forfeited_wei"] == 2 * BOND, 2 * BOND, a["forfeited_wei"])
        self.report.check(section, "A refunded six bonds", a["refunded_wei"] == 6 * BOND, 6 * BOND,
                          a["refunded_wei"])
        self.report.check(section, "B paid its one reward and forfeited india's two abandoned bonds",
                          b["paid_wei"] == REWARDS_B[2] and b["forfeited_wei"] == EXTRA_SEALS * BOND,
                          f"paid {REWARDS_B[2]}, forfeited {EXTRA_SEALS * BOND}",
                          f"paid {b['paid_wei']}, forfeited {b['forfeited_wei']}")
        b_rows = self.view("get_findings", [self.funder, self.bounty_ids["B"]])["findings"]
        self.report.check(section, "india's extra findings ended as unrevealed_bond_forfeited",
                          [row["outcome"] for row in b_rows[1:]] == ["unrevealed_bond_forfeited"] * EXTRA_SEALS,
                          ["unrevealed_bond_forfeited"] * EXTRA_SEALS, [row["outcome"] for row in b_rows[1:]])
        for which, want in (("A", list(REWARDS_A)), ("B", [REWARDS_B[2]])):
            sheet = self.view("get_errata", [self.funder, self.bounty_ids[which]])
            got = [e["reward_wei"] for e in sheet["entries"]]
            self.report.check(section, f"the settled errata sheet of {which} lists exactly the paid defects",
                              sheet["finalized"] is True and got == want, want, got)
        delta = self.finding("delta")
        self.report.check(section, "delta is recorded as a duplicate of the first finding",
                          delta["outcome"] == "duplicate_bond_refunded" and delta["duplicate_of"] == 1,
                          "duplicate_bond_refunded of #1", f"{delta['outcome']} of #{delta['duplicate_of']}")
        for name, outcome in (("alpha", "rewarded"), ("echo", "not_upheld_bond_refunded"),
                              ("foxtrot", "disqualified_bond_forfeited"),
                              ("hotel", "unrevealed_bond_forfeited"), ("india", "rewarded")):
            self.report.check(section, f"{name} outcome is {outcome}",
                              self.finding(name)["outcome"] == outcome, outcome,
                              self.finding(name)["outcome"])
        for name, wei in EXPECTED_CREDIT.items():
            got = self.credit(name)
            self.report.check(section, f"{name} credit", got == wei, wei, got)
        accounting = self.view("get_accounting")
        total = sum(EXPECTED_CREDIT.values())
        credits_delta = self.delta(accounting, "total_credits")
        locked_delta = (self.delta(accounting, "total_locked_budgets")
                        + self.delta(accounting, "total_locked_bonds"))
        self.report.check(section, "ledger conserves all deposited value (change since run start)",
                          credits_delta == total and locked_delta == 0, f"credits +{total}, locked +0",
                          f"credits +{credits_delta}, locked +{locked_delta}")
        self.report.check(section, "contract balance covers every liability",
                          accounting["solvent"], True, accounting["solvent"])
        self.state["expected_credit"] = EXPECTED_CREDIT
        self.report.sections[section] = {"A": a, "B": b, "accounting": accounting,
                                         "expected_credit": EXPECTED_CREDIT}

    def phase_withdraw(self) -> None:
        section = "7. Native GEN withdrawals"
        expected = self.state["expected_credit"]
        for name, wei in expected.items():
            if wei == 0 or self.state.setdefault("withdrawn", {}).get(name):
                continue
            before = self.chain.balance(name)
            self.tx(name, "withdraw_credit", [], label=f"{name} withdraws credit", section=section)
            deadline = time.time() + 240
            after = before
            while time.time() < deadline:
                after = self.chain.balance(name)
                if after - before >= wei:
                    break
                time.sleep(8)
            gained = after - before
            self.report.check(section, f"{name} received {wei} wei", gained == wei, wei, gained)
            self.report.check(section, f"{name} ledger credit cleared", self.credit(name) == 0, 0,
                              self.credit(name))
            self.state["withdrawn"][name] = True
            self.save()
        for name in ("foxtrot", "hotel"):
            self.revert_check(section, f"{name} has nothing to withdraw", name, "withdraw_credit", [])
        accounting = self.view("get_accounting")
        liabilities_delta = self.delta(accounting, "accounted_liabilities")
        self.report.check(section, "no liabilities remain from this run (change since run start)",
                          liabilities_delta == 0, 0, liabilities_delta)
        self.report.sections[section] = {"accounting": accounting}

    def run(self) -> None:
        for phase in (self.phase_funding, self.phase_open, self.phase_commit, self.phase_reveal,
                      self.phase_screening, self.phase_finalize, self.phase_withdraw):
            phase()


# -------------------------------------------------------------------------------- report


def write_report(report: Report, directory: Path, contract: str, base_url: str) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = directory / f"live-e2e-{stamp}.json"
    md_path = directory / f"live-e2e-{stamp}.md"
    passed = sum(1 for c in report.checks if c.passed)
    failed = len(report.checks) - passed
    payload = {
        "started": report.started, "finished": report.finished, "contract": contract,
        "fixture_base_url": base_url, "environment": report.environment,
        "passed": passed, "failed": failed, "fatal": report.fatal,
        "checks": [c.__dict__ for c in report.checks],
        "transactions": [dict(t.__dict__, link=t.link) for t in report.txs],
        "sections": report.sections,
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str))

    verdict = "PASS" if report.all_passed else "FAIL"
    lines = [
        f"# Errata live end-to-end report - {verdict}", "",
        "- Network: GenLayer Studionet",
        f"- Contract: [{contract}]({EXPLORER_ADDRESS}{contract})",
        f"- Started / finished (UTC): {report.started} / {report.finished}",
        f"- Checks: **{passed} passed, {failed} failed**",
        f"- Fixture base URL: `{base_url}`",
    ]
    for key, value in report.environment.items():
        lines.append(f"- {key}: `{value}`")
    if report.fatal:
        lines += ["", "## Run aborted", "", "```", report.fatal, "```"]
    lines += ["", "## Checks", "", "| Result | Section | Check | Expected | Observed |",
              "| --- | --- | --- | --- | --- |"]
    for check in report.checks:
        mark = "PASS" if check.passed else "**FAIL**"
        row = [mark, check.section, check.name, check.expected, check.observed]
        lines.append("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in row) + " |")
    lines += ["", "## Transactions", "", "| # | Step | Account | Function | Result | Status | Seconds | Tx |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for number, tx in enumerate(report.txs, 1):
        link = f"[{tx.tx_hash[:12]}...]({tx.link})" if tx.tx_hash else (tx.note or "-")
        expected = " (expected revert)" if tx.expected == "revert" else ""
        lines.append(f"| {number} | {tx.label}{expected} | `{tx.account[:10]}...` | `{tx.function}` | "
                     f"{tx.result or '-'} | {tx.status or '-'} | {tx.seconds} | {link} |")
    findings = report.sections.get("5. Validator screening", {}).get("findings", {})
    if findings:
        lines += ["", "## Validator findings", "",
                  "| Finder | Status | Kind | Assessments | Charged calls | Reason | Rationale |",
                  "| --- | --- | --- | --- | --- | --- | --- |"]
        for name, state in findings.items():
            lines.append(f"| {name} | {state['status']} | {state['kind_name']} | {state['attempts']} | "
                         f"{state['review_calls']} | {state['reason'] or '-'} | "
                         f"{str(state['rationale']).replace('|', '/')} |")
    lines += ["", "## Notes", "",
              "- Pass/fail is decided from state read back from the contract, not from receipts.",
              "- `FINISHED_WITH_ERROR` on a step marked *expected revert* is the desired outcome.",
              "- Verdicts come from real validators and a real LLM; a failed screening check can mean "
              "the model disagreed with the fixture, so read the rationale before suspecting the contract."]
    md_path.write_text("\n".join(lines) + "\n")
    return md_path, json_path


# ---------------------------------------------------------------------------------- main


def environment_info(chain_name: str) -> dict:
    info = {"python": platform.python_version(), "platform": platform.platform(),
            "network": chain_name, "git_commit": git_output("rev-parse", "HEAD") or "unknown",
            "contract_sha256": hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest()}
    for package in ("genlayer-py", "genlayer-test"):
        try:
            from importlib.metadata import version
            info[package] = version(package)
        except Exception:  # noqa: BLE001
            info[package] = "not installed"
    return info


def load_state(path: Path, resume: str | None) -> tuple[dict, Path]:
    if resume:
        candidate = STATE_DIR / f"run-{resume}.json"
        if not candidate.exists():
            raise ChainError(f"No saved run {resume} in {STATE_DIR}")
        return json.loads(candidate.read_text()), candidate
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    state = {"run_id": run_id, "bounty_a": f"LIVE-{run_id}-A", "bounty_b": f"LIVE-{run_id}-B",
             "salts": {n: new_salt() for n in FINDERS}, "keys": {}}
    keys_file = STATE_DIR / "accounts.json"
    if keys_file.exists():
        state["keys"] = json.loads(keys_file.read_text())
    return state, STATE_DIR / f"run-{run_id}.json"


def main(argv: list[str] | None = None, chain_factory: Callable | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--address", default=os.environ.get("ERRATA_ADDRESS"),
                        help="deployed Errata contract (or env ERRATA_ADDRESS)")
    parser.add_argument("--deploy", action="store_true", help="deploy contracts/errata.py first")
    parser.add_argument("--fixture-base-url", default=os.environ.get("FIXTURE_BASE_URL"))
    parser.add_argument("--skip-fixture-check", action="store_true")
    parser.add_argument("--commit-seconds", type=int, default=1800)
    parser.add_argument("--reveal-seconds", type=int, default=1000)
    parser.add_argument("--wait-seconds", type=int, default=900, help="per-transaction wait budget")
    parser.add_argument("--rpc-url", default=os.environ.get("GENLAYER_RPC_URL"))
    parser.add_argument("--resume", help="run id from a previous (interrupted) run")
    parser.add_argument("--check-only", action="store_true",
                        help="verify tooling, fixtures and RPC reachability, then stop")
    parser.add_argument("--probe-nonpayable", action="store_true",
                        help="also check that GEN sent to a non-payable method is refused. Informational:\n"
                             "how the network treats the attached value is not part of the contract")
    args = parser.parse_args(argv)

    report = Report()
    report.environment = environment_info("studionet")
    contract = args.address or ""
    base_url = ""
    exit_code = 1
    try:
        state, state_path = load_state(STATE_DIR / "accounts.json", args.resume)
        base_url = args.fixture_base_url and fixture_base_url(args.fixture_base_url) \
            or state.get("fixture_base_url") or fixture_base_url(None)
        state["fixture_base_url"] = base_url
        fixtures = load_fixtures(base_url, verify=not args.skip_fixture_check)
        log(f"fixtures verified at {base_url}")
        if not contract and not args.deploy and not chain_factory:
            raise ChainError("Pass --address of your deployed Errata contract, or use --deploy.")
        chain = (chain_factory(state) if chain_factory
                 else GenLayerChain(contract or "0x" + "0" * 40, state, args.rpc_url, args.wait_seconds))
        if args.deploy and not chain_factory:
            log("deploying contracts/errata.py")
            chain.fund("funder", 2 * 10**18)  # best effort; the deployer needs gas on some networks
            contract = chain.deploy(CONTRACT_PATH.read_text())
            chain.contract = contract
            log(f"deployed at {contract}")
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / "accounts.json").write_text(json.dumps(state["keys"], indent=2))
        info = chain.read("get_info", [])
        report.check("0. Preflight", "contract answers get_info as Errata", info.get("name") == "Errata",
                     "Errata", info.get("name"))
        report.environment["contract_version"] = info.get("version")
        if args.check_only:
            Scenario(chain, report, fixtures, state, args.commit_seconds, args.reveal_seconds,
                     state_path).print_funding_plan()
            log("check-only run finished: tooling, fixtures and contract are reachable")
            return 0 if report.all_passed else 1
        scenario = Scenario(chain, report, fixtures, state, args.commit_seconds, args.reveal_seconds,
                            state_path, probe_nonpayable=args.probe_nonpayable)
        scenario.run()
        exit_code = 0 if report.all_passed else 1
    except ChainError as error:
        report.fatal = str(error)
        exit_code = 2
    except KeyboardInterrupt:
        report.fatal = "Interrupted. Resume with --resume " + (locals().get("state", {}) or {}).get("run_id", "")
        exit_code = 130
    except Exception:  # noqa: BLE001
        report.fatal = traceback.format_exc()
        exit_code = 2
    finally:
        report.finished = utc_now()
        if report.checks or report.fatal:
            md_path, json_path = write_report(report, REPORT_DIR, contract or "(none)", base_url)
            log(f"report written: {os.path.relpath(md_path)} and {json_path.name}")
        if report.fatal:
            log("ABORTED: " + report.fatal.strip().splitlines()[-1])
        passed = sum(1 for c in report.checks if c.passed)
        log(f"{passed}/{len(report.checks)} checks passed")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
