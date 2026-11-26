#!/usr/bin/env python3
"""Client-side helpers for Errata publishers and finders.

Everything here runs locally. Computing a commitment locally is the only safe way to do
it: sending the quotes and the salt to any remote service (including an RPC node) before
the reveal window would leak the sealed finding.

Command line:

    python scripts/errata_tools.py hash handbook.md
    python scripts/errata_tools.py check handbook.md --quote-a "..." --quote-b "..." [--subject-hash 0x...]
    python scripts/errata_tools.py seal handbook.md --subject-hash 0x... --publisher 0x... \\
        --bounty-id B-1 --finder 0x... --kind 2 --quote-a "..." --quote-b "..." \\
        [--note "..."] [--mirror https://...]

--subject-hash is the `subject_hash` that the bounty shows on chain. A finder MUST verify that the
document they read hashes to it: a publisher who serves different text than they pinned would
otherwise make every honest quote "not found", and a quote the contract cannot find forfeits the bond.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import re
import secrets
import sys
from pathlib import Path
from urllib.parse import urlsplit

COMMITMENT_DOMAIN = "errata-finding-v1"
SALT_PATTERN = re.compile(r"[A-Za-z0-9_.~-]{16,64}")
MIN_QUOTE = 8
MAX_QUOTE = 120
MAX_NOTE = 240
MAX_SUBJECT_BYTES = 100_000
MAX_URL = 512
ADDRESS_PATTERN = re.compile(r"0x[0-9a-fA-F]{40}")
BOUNTY_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
KIND_NAMES = {1: "arithmetic", 2: "contradiction", 3: "definition_conflict"}


def sha256_hex(raw: bytes) -> str:
    """0x-prefixed lowercase SHA-256, the format the contract expects."""
    return "0x" + hashlib.sha256(raw).hexdigest()


def new_salt() -> str:
    """A fresh 43-character URL-safe salt with 256 bits of entropy."""
    return secrets.token_urlsafe(32)


def clean(value: str) -> str:
    """The contract's whitespace collapse: NUL becomes a space, runs of whitespace become one."""
    return " ".join(str(value).replace("\x00", " ").split())


def bounty_key(publisher_address: str, bounty_id: str) -> str:
    return publisher_address.lower() + ":" + bounty_id


def commitment(
    publisher_address: str,
    bounty_id: str,
    finder_address: str,
    kind: int,
    quote_a: str,
    quote_b: str,
    note: str,
    mirror_url: str,
    salt: str,
) -> str:
    """Return the sealed-finding commitment exactly as the contract recomputes it."""
    if not SALT_PATTERN.fullmatch(salt):
        raise ValueError("salt must be 16-64 characters from [A-Za-z0-9_.~-]")
    if kind not in KIND_NAMES:
        raise ValueError("kind must be 1 (arithmetic), 2 (contradiction) or 3 (definition_conflict)")
    fields = [
        COMMITMENT_DOMAIN,
        bounty_key(publisher_address, bounty_id),
        finder_address.lower(),
        str(int(kind)),
        clean(quote_a),
        clean(quote_b),
        clean(note),
        mirror_url.strip(),
        salt,
    ]
    data = b""
    for field in fields:
        encoded = field.encode("utf-8")
        data += len(encoded).to_bytes(4, "big") + encoded
    return "0x" + hashlib.sha256(data).hexdigest()


def subject_text(raw: bytes) -> str:
    """The whitespace-collapsed text that the contract searches for quotes."""
    if not raw or len(raw) > MAX_SUBJECT_BYTES:
        raise ValueError(f"subject must be 1..{MAX_SUBJECT_BYTES} bytes")
    return clean(raw.decode("utf-8"))


def _occurrences(text: str, quote: str) -> int:
    first = text.find(quote)
    if first < 0:
        return 0
    return 1 if text.find(quote, first + 1) < 0 else 2


def mirror_problem(url: str) -> str:
    """Why the contract would refuse this mirror URL at reveal time ('' when it is acceptable).

    Mirrors the contract's `_host` rules. The mirror is part of the sealed commitment, so a mirror the
    contract refuses can never be revealed: the bond would be forfeited as an unrevealed finding.
    """
    text = str(url).strip()
    if not text:
        return ""
    if len(text) > MAX_URL or any(ord(c) <= 32 or ord(c) == 127 for c in text):
        return "mirror must be a plain HTTPS URL of at most 512 characters without spaces"
    try:
        parsed = urlsplit(text)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        return "mirror is not a valid URL"
    if (parsed.scheme.lower() != "https" or not hostname or parsed.username is not None
            or parsed.password is not None or port not in (None, 443) or parsed.fragment):
        return "mirror must be https://host/path: no credentials, no port other than 443, no #fragment"
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal", ".test")):
        return "mirror must not point at a local host"
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        return "mirror must use a host name, not an IP address"
    labels = hostname.split(".")
    if (len(hostname) > 253 or len(labels) < 2 or not re.fullmatch(r"[a-z]{2,63}", labels[-1])
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)):
        return "mirror host name is not valid"
    return ""


def seal_input_problems(publisher: str, bounty_id: str, finder: str, mirror: str) -> list[str]:
    """Inputs that would make the contract fail to recompute the commitment, or refuse the reveal."""
    problems: list[str] = []
    if not ADDRESS_PATTERN.fullmatch(publisher):
        problems.append("publisher must be a 0x address of 40 hex digits")
    if not ADDRESS_PATTERN.fullmatch(finder):
        problems.append("finder must be a 0x address of 40 hex digits")
    if not BOUNTY_ID_PATTERN.fullmatch(bounty_id):
        problems.append("bounty id must be 1-64 characters from [A-Za-z0-9_.-], starting with a letter or digit")
    reason = mirror_problem(mirror)
    if reason:
        problems.append(reason)
    return problems


def check_quotes(raw_subject: bytes, quote_a: str, quote_b: str,
                 expected_hash: str = "") -> list[str]:
    """Every reason the contract would refuse or disqualify these quotes. Empty means fine.

    expected_hash is the bounty's on-chain `subject_hash`; when given, the document must match it.
    """
    problems: list[str] = []
    if expected_hash and sha256_hex(raw_subject) != expected_hash.strip().lower():
        problems.append(
            f"this document hashes to {sha256_hex(raw_subject)}, not to the bounty's pinned "
            f"{expected_hash.strip().lower()}; the validators will never see this text"
        )
    try:
        text = subject_text(raw_subject)
    except (ValueError, UnicodeDecodeError) as error:
        return [f"subject cannot be read: {error}"]
    if not text:
        return ["subject is empty after whitespace collapse"]
    a = clean(quote_a)
    b = clean(quote_b)
    for label, quote in (("A", a), ("B", b)):
        if not MIN_QUOTE <= len(quote) <= MAX_QUOTE:
            problems.append(f"quote {label} must be {MIN_QUOTE}..{MAX_QUOTE} characters "
                            f"after whitespace collapse (it has {len(quote)})")
    if a in b or b in a:
        problems.append("quotes must be distinct passages (one contains the other)")
    seen = {}
    for label, quote in (("A", a), ("B", b)):
        count = _occurrences(text, quote)
        if count == 0:
            problems.append(f"quote {label} is not found in the subject (matching is exact and "
                            "case-sensitive)")
        elif count > 1:
            problems.append(f"quote {label} occurs more than once; extend it until it is unique")
        else:
            start = text.find(quote)
            seen[label] = (start, start + len(quote))
    if len(seen) == 2 and seen["A"][0] < seen["B"][1] and seen["B"][0] < seen["A"][1]:
        problems.append("quotes overlap each other in the subject")
    return problems


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    h = sub.add_parser("hash", help="print the 0x SHA-256 of a file")
    h.add_argument("file")
    c = sub.add_parser("check", help="verify quotes against a subject file")
    c.add_argument("file")
    c.add_argument("--quote-a", required=True)
    c.add_argument("--quote-b", required=True)
    c.add_argument("--subject-hash", default="", help="the bounty's on-chain subject_hash (recommended)")
    s = sub.add_parser("seal", help="verify quotes and print the commitment plus a fresh salt")
    s.add_argument("file")
    s.add_argument("--subject-hash", required=True,
                   help="the bounty's on-chain subject_hash; the document must match it")
    s.add_argument("--publisher", required=True)
    s.add_argument("--bounty-id", required=True)
    s.add_argument("--finder", required=True)
    s.add_argument("--kind", type=int, required=True, choices=sorted(KIND_NAMES))
    s.add_argument("--quote-a", required=True)
    s.add_argument("--quote-b", required=True)
    s.add_argument("--note", default="")
    s.add_argument("--mirror", default="")
    s.add_argument("--salt", default="")
    args = parser.parse_args(argv)

    raw = Path(args.file).read_bytes()
    if args.command == "hash":
        print(sha256_hex(raw))
        return 0
    problems = check_quotes(raw, args.quote_a, args.quote_b, args.subject_hash)
    if args.command == "check":
        for problem in problems:
            print("PROBLEM:", problem)
        print("OK: both quotes are valid" if not problems else "NOT OK")
        return 1 if problems else 0
    publisher = args.publisher.strip()
    bounty_id = args.bounty_id.strip()
    finder = args.finder.strip()
    mirror = args.mirror.strip()
    problems += seal_input_problems(publisher, bounty_id, finder, mirror)
    if problems:
        for problem in problems:
            print("PROBLEM:", problem, file=sys.stderr)
        print("Refusing to seal a finding the contract would reject, disqualify or never let you reveal.",
              file=sys.stderr)
        return 1
    if len(clean(args.note)) > MAX_NOTE:
        print(f"PROBLEM: note longer than {MAX_NOTE} characters", file=sys.stderr)
        return 1
    salt = args.salt or new_salt()
    print("commitment:", commitment(publisher, bounty_id, finder, args.kind,
                                    args.quote_a, args.quote_b, args.note, mirror, salt))
    print("salt:      ", salt, "  (keep it secret until you reveal)")
    print("subject:   ", sha256_hex(raw))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
