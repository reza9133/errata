"""Seeded random markets: whatever happens, the ledger and the settlement rules hold."""

import random

import pytest

from tests.direct.support import (
    ARITH, D_FEE, D_FEE_VARIANT, D_LATE, D_MEETING, D_MEMBER, MISSING, NOW_TS, OK_HALL, OK_MINUTES,
    OVERLAPPING, SUBJECT, SUBJECT_HASH, SUBJECT_URL, AMBIGUOUS, Spec, addr, at, check_books,
    deploy, set_web, slot_of,
)

PAIRS = [D_FEE, D_FEE_VARIANT, (D_FEE[1], D_FEE[0]), D_MEETING, D_MEMBER, D_LATE, OK_HALL,
         OK_MINUTES, MISSING, AMBIGUOUS, OVERLAPPING, (D_FEE[0], D_LATE[1]), (D_MEETING[0], D_MEMBER[1])]
ANSWERS = ["upheld", "upheld", "upheld", "not_upheld", "unclear", "garbage"]
STEPS = 90


def _is_expected_revert(error):
    return type(error).__name__ == "UserError"


def _same_defect(left, right):
    def overlap(a, b):
        return a[0] < b[1] and b[0] < a[1]
    la, lb = (left[0], left[1]), (left[2], left[3])
    ra, rb = (right[0], right[1]), (right[2], right[3])
    return (overlap(la, ra) and overlap(lb, rb)) or (overlap(la, rb) and overlap(lb, ra))


@pytest.mark.parametrize("seed", range(10))
def test_random_markets_keep_every_invariant(direct_vm, direct_deploy, actors, seed):
    """Three waves of two concurrent bounties, with randomised details and hostile mistakes."""
    rng = random.Random(seed * 7919 + 13)
    contract = deploy(direct_vm, direct_deploy)
    publishers = actors[0:2]
    finders = actors[2:9]
    everyone = actors[:10]
    state = {"clock": NOW_TS, "deposited": 0, "withdrawn": 0}
    bounties = {}
    stats = {}
    set_web(direct_vm, SUBJECT_URL, {"status": 200, "body": SUBJECT})

    def attempt(who, fn, label):
        before = contract.get_accounting()
        direct_vm.sender = who
        direct_vm.value = 0
        try:
            fn()
        except Exception as error:  # noqa: BLE001
            assert _is_expected_revert(error), f"unexpected crash: {type(error).__name__}: {error}"
            assert contract.get_accounting() == before, "a reverted call must not move money"
            stats[label + "_reverted"] = stats.get(label + "_reverted", 0) + 1
            return False
        stats[label] = stats.get(label, 0) + 1
        return True

    def fund(who, amount):
        direct_vm.sender = who
        direct_vm.value = amount
        contract.deposit_credit()
        direct_vm.value = 0
        state["deposited"] += amount

    def books():
        accounting = check_books(contract, everyone, list(bounties))
        assert accounting["accounted_liabilities"] == state["deposited"] - state["withdrawn"]

    def warp(to):
        state["clock"] = max(state["clock"], to)
        direct_vm.warp(at(state["clock"]))

    for publisher in publishers:
        fund(publisher, 60 * 10**15)

    def open_one(publisher, tag):
        bounty_id = f"R{tag}"
        rewards = [rng.choice([0, 10**14, 3 * 10**14, 5 * 10**14]) for _ in range(3)]
        if not any(rewards):
            rewards[rng.randrange(3)] = 4 * 10**14
        enabled = [value for value in rewards if value]
        budget = rng.choice([10**15, 15 * 10**14, 3 * 10**15])
        bond = min(rng.choice([10**13, 10**14, min(enabled)]), min(enabled))
        capacity = rng.choice([1, 2, 3, 4, 8, 16])
        commit_by = state["clock"] + rng.randint(300, 900)
        reveal_by = commit_by + rng.randint(300, 900)
        if not attempt(publisher, lambda: contract.open_bounty(
                bounty_id, "fuzz", SUBJECT_URL, SUBJECT_HASH, *rewards, budget, bond, capacity,
                commit_by, reveal_by), "open"):
            raise AssertionError("a valid bounty must open")
        info = dict(commit_by=commit_by, reveal_by=reveal_by, review_by=reveal_by + 3 * 86400,
                    specs={}, bond=bond, kinds=[i + 1 for i, v in enumerate(rewards) if v],
                    publisher=publisher, id=bounty_id)
        bounties[(publisher, bounty_id)] = info
        return info

    def wrong_time_noise(info):
        """Deliberately mistimed calls that must all revert without touching money."""
        who = rng.choice(finders + [info["publisher"]])
        publisher, bounty_id = info["publisher"], info["id"]
        probe = rng.choice(["finalize", "review", "reveal", "commit"])
        phase_ok = {
            "finalize": state["clock"] >= info["reveal_by"],
            "review": info["reveal_by"] <= state["clock"] < info["review_by"],
            "reveal": info["commit_by"] <= state["clock"] < info["reveal_by"],
            "commit": state["clock"] < info["commit_by"],
        }[probe]
        if phase_ok:
            return
        spec = Spec("noise", who, ARITH, D_FEE, salt="n" * 40)
        calls = {
            "finalize": lambda: contract.finalize_bounty(addr(publisher), bounty_id),
            "review": lambda: contract.review_finding(addr(publisher), bounty_id, 0),
            "reveal": lambda: contract.reveal_finding(addr(publisher), bounty_id, 0, ARITH, D_FEE[0],
                                                       D_FEE[1], "", "", spec.salt),
            "commit": lambda: contract.commit_finding(addr(publisher), bounty_id,
                                                      spec.seal(publisher, bounty_id)),
        }
        if probe == "finalize" and not info["specs"] and state["clock"] < info["reveal_by"]:
            pass
        attempt(who, calls[probe], "noise_" + probe)

    for wave in range(3):
        for publisher in publishers:  # earlier cash-outs may have emptied them
            if contract.get_available_credit(addr(publisher)) < 4 * 10**15:
                fund(publisher, 10**16)
        active = [open_one(publishers[0], f"{wave}a"), open_one(publishers[1], f"{wave}b")]
        books()
        # -- commit phase
        for _ in range(rng.randint(3, 11)):
            info = rng.choice(active)
            finder = rng.choice(finders)
            kind = rng.choice(info["kinds"])
            spec = Spec(f"{seed}-{wave}-{len(info['specs'])}-{finder.hex()}", finder, kind,
                        rng.choice(PAIRS), answer=rng.choice(ANSWERS),
                        salt="fz" + finder.hex()[:20] + str(len(info["specs"])).zfill(4) + "x" * 8)
            if rng.random() < 0.92:
                fund(finder, info["bond"])
            sealed = {}

            def seal_it():
                sealed["index"] = contract.commit_finding(
                    addr(info["publisher"]), info["id"], spec.seal(info["publisher"], info["id"]))

            if attempt(finder, seal_it, "commit"):
                spec.slots[(addr(info["publisher"]), info["id"])] = sealed["index"]
                info["specs"][spec.name] = (finder, spec)
            if rng.random() < 0.15 and info["specs"]:
                quitter, quit_spec = rng.choice(list(info["specs"].values()))
                attempt(quitter, lambda: contract.withdraw_finding(
                    addr(info["publisher"]), info["id"],
                    slot_of(quit_spec, info["publisher"], info["id"])), "withdraw_finding")
            if rng.random() < 0.3:
                wrong_time_noise(rng.choice(active))
            warp(state["clock"] + rng.randint(1, 20))
            books()
        # -- reveal phase
        warp(max(info["commit_by"] for info in active) + rng.randint(0, 3))
        for info in active:
            for finder, spec in rng.sample(list(info["specs"].values()), len(info["specs"])):
                roll = rng.random()
                if roll < 0.08:
                    continue  # never reveals
                salt = "wrong" * 8 if roll < 0.18 else spec.salt
                if attempt(finder, lambda: contract.reveal_finding(
                        addr(info["publisher"]), info["id"],
                        slot_of(spec, info["publisher"], info["id"]), spec.kind, spec.quote_a,
                        spec.quote_b, "", "", salt), "reveal"):
                    direct_vm.mock_llm(spec.pattern(), spec.answer_json())
                if rng.random() < 0.2:
                    wrong_time_noise(info)
                books()
        # -- review phase
        warp(max(info["reveal_by"] for info in active) + rng.randint(0, 3))
        for _ in range(rng.randint(8, 30)):
            info = rng.choice(active)
            if info["specs"]:
                finder, spec = rng.choice(list(info["specs"].values()))
                if rng.random() < 0.3:
                    spec.answer = rng.choice(ANSWERS)  # the model changes its mind between attempts
                direct_vm.mock_llm(spec.pattern(), spec.answer_json())
                set_web(direct_vm, SUBJECT_URL, {"status": 200, "body": SUBJECT}
                        if rng.random() < 0.85 else {"status": 503, "body": b"down"})
                caller = rng.choice([info["publisher"], finder, finder, rng.choice(finders)])
                attempt(caller, lambda: contract.review_finding(
                    addr(info["publisher"]), info["id"], slot_of(spec, info["publisher"], info["id"])),
                    "review")
            if rng.random() < 0.1:
                wrong_time_noise(info)
            warp(state["clock"] + rng.choice([5, 30, 61, 130, 250, 500, 1000, 4000]))
            books()
        # -- settlement, some early and some after the review window
        set_web(direct_vm, SUBJECT_URL, {"status": 200, "body": SUBJECT})
        if rng.random() < 0.5:
            for info in active:
                preview = contract.get_standing(addr(info["publisher"]), info["id"])
                sheet = contract.get_errata(addr(info["publisher"]), info["id"])
                if attempt(rng.choice(everyone), lambda: contract.finalize_bounty(
                        addr(info["publisher"]), info["id"]), "finalize"):
                    _check_finalized(contract, info["publisher"], info["id"], preview, sheet)
        warp(max(info["review_by"] for info in active))
        for info in active:
            if contract.get_bounty(addr(info["publisher"]), info["id"])["status"] == "open":
                preview = contract.get_standing(addr(info["publisher"]), info["id"])
                sheet = contract.get_errata(addr(info["publisher"]), info["id"])
                direct_vm.sender = everyone[9]
                contract.finalize_bounty(addr(info["publisher"]), info["id"])
                stats["finalize"] = stats.get("finalize", 0) + 1
                _check_finalized(contract, info["publisher"], info["id"], preview, sheet)
            books()
        # -- cash out some accounts mid-run
        for who in rng.sample(everyone, 3):
            amount = contract.get_available_credit(addr(who))
            if amount > 0 and attempt(who, lambda: contract.withdraw_credit(), "withdraw_credit"):
                state["withdrawn"] += amount
        books()

    print("SEED", seed, dict(sorted(stats.items())))
    final = contract.get_accounting()
    assert final["total_locked_budgets"] == 0 and final["total_locked_bonds"] == 0
    for who in everyone:
        amount = contract.get_available_credit(addr(who))
        if amount > 0:
            direct_vm.sender = who
            contract.withdraw_credit()
            state["withdrawn"] += amount
    assert contract.get_accounting()["accounted_liabilities"] == 0
    assert state["deposited"] == state["withdrawn"]


def _check_finalized(contract, publisher, bounty_id, preview, sheet=None):
    bounty = contract.get_bounty(addr(publisher), bounty_id)
    assert bounty["status"] in ("finalized", "cancelled")
    if bounty["status"] == "cancelled":
        return
    states = contract.get_findings(addr(publisher), bounty_id)["findings"]
    assert [s["index"] for s in states] == list(range(len(states)))
    per_account = {}
    for s in states:
        per_account[s["finder"]] = per_account.get(s["finder"], 0) + 1
    assert max(per_account.values(), default=0) <= 3, "the per-account cap holds"
    assert bounty["paid_wei"] <= bounty["budget_wei"]
    assert bounty["paid_wei"] + bounty["returned_wei"] == bounty["budget_wei"]
    assert bounty["paid_wei"] == sum(s["reward_wei"] for s in states)
    assert bounty["rewarded_count"] == sum(1 for s in states if s["reward_wei"] > 0)
    assert all(s["outcome"] for s in states), "every finding ends with a recorded outcome"
    assert all(s["bond_wei"] == 0 for s in states), "no bond stays locked after finalization"
    rewarded = [s for s in states if s["reward_wei"] > 0]
    for i, left in enumerate(rewarded):
        assert left["status"] == "upheld"
        assert left["reward_wei"] <= max(bounty["reward_arithmetic"], bounty["reward_contradiction"],
                                         bounty["reward_definition"])
        for right in rewarded[i + 1:]:
            spans_l = (left["a_start"], left["a_end"], left["b_start"], left["b_end"])
            spans_r = (right["a_start"], right["a_end"], right["b_start"], right["b_end"])
            assert not _same_defect(spans_l, spans_r), "two rewarded findings share one defect"
    for s in states:
        if s["outcome"] == "duplicate_bond_refunded":
            assert s["duplicate_of"] >= 1 and s["reward_wei"] == 0
            earlier = states[s["duplicate_of"] - 1]
            assert earlier["index"] < s["index"] and earlier["status"] == "upheld"
    bonds = sum(1 for s in states if s["outcome"] != "withdrawn_bond_refunded") * bounty["bond_wei"]
    assert bounty["forfeited_wei"] + bounty["refunded_wei"] == bonds
    if preview is not None:
        assert [e["projected_outcome"] for e in preview["entries"]] == [s["outcome"] for s in states]
        assert [e["projected_reward"] for e in preview["entries"]] == [s["reward_wei"] for s in states]
        assert preview["projected_paid"] == bounty["paid_wei"]
        assert preview["projected_forfeited"] == bounty["forfeited_wei"]
    # The published errata sheet is the settled result, and it was projected correctly beforehand.
    accepted = [x for x in states if x["outcome"] in ("rewarded", "upheld_budget_exhausted_bond_refunded")]
    settled_sheet = contract.get_errata(addr(publisher), bounty_id)
    assert settled_sheet["finalized"] is True
    assert [e["index"] for e in settled_sheet["entries"]] == [x["index"] for x in accepted]
    assert [e["reward_wei"] for e in settled_sheet["entries"]] == [x["reward_wei"] for x in accepted]
    if sheet is not None:
        assert sheet["finalized"] is False and sheet["entries"] == settled_sheet["entries"]
    after = contract.get_standing(addr(publisher), bounty_id)
    assert after["finalized"] is True
    assert [e["projected_outcome"] for e in after["entries"]] == [x["outcome"] for x in states]
