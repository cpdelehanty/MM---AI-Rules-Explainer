"""
Focused A/B: re-run ONLY the questions that failed in the baseline,
with the new pipeline (rerank + critique + bigger Bora Bora chunks).

Faster than re-running all 930 — typically 91 questions, ~35 minutes.
Tells us quickly whether the new pipeline addresses the failure modes.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv(override=True)

from anthropic import Anthropic
import voyageai

from database import get_game_chunks
from rules_pipeline import run_pipeline
from groundedness_test import judge_answer, VOYAGE_DELAY


BASELINE_FILE = "groundedness_results_baseline.json"
RESULTS_FILE = "groundedness_results_failures_v2.json"

PASS = {"correct", "evasive_ok"}


def save(data):
    tmp = RESULTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, RESULTS_FILE)


def main():
    with open(BASELINE_FILE, encoding="utf-8") as f:
        baseline_qs = json.load(f)["questions"]

    failures = [q for q in baseline_qs
                if "verdict" in q and q["verdict"] not in PASS]
    print(f"Baseline failures: {len(failures)} (out of {len(baseline_qs)})")

    voyage_client = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])
    claude_client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    if os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE, encoding="utf-8") as f:
            results = json.load(f)
        done = {(q["game"], q["question"]) for q in results.get("questions", [])
                if "verdict" in q}
    else:
        results = {"started_at": datetime.now(timezone.utc).isoformat(),
                   "questions": []}
        save(results)
        done = set()

    last_voyage = 0.0
    chunks_cache = {}

    for i, b in enumerate(failures, 1):
        game, question = b["game"], b["question"]
        if (game, question) in done:
            continue

        if game not in chunks_cache:
            chunks_cache[game] = get_game_chunks(game)
        chunks = chunks_cache[game]
        if not chunks:
            continue

        elapsed = time.time() - last_voyage
        if elapsed < VOYAGE_DELAY:
            time.sleep(VOYAGE_DELAY - elapsed)

        try:
            out = run_pipeline(voyage_client, claude_client, question,
                                game, chunks,
                                use_rerank=True, use_critique=True)
            last_voyage = time.time()
            judge = judge_answer(claude_client, game, question,
                                  out["answer"], out["top_chunks"])
        except Exception as e:
            print(f"  ! q{i} failed: {e}")
            results["questions"].append({
                "game": game, "question": question, "error": str(e),
                "baseline_verdict": b.get("verdict"),
            })
            save(results)
            continue

        old_v = b.get("verdict", "?")
        new_v = judge.get("verdict", "?")
        old_pass = old_v in PASS
        new_pass = new_v in PASS
        if not old_pass and new_pass:
            arrow = "FIXED"
        elif old_pass and not new_pass:
            arrow = "BROKE"
        elif new_v != old_v:
            arrow = f"{old_v}->{new_v}"
        else:
            arrow = "same"

        flags = ""
        if out["rerank_used"]: flags += "R"
        if out["critique_changed"]: flags += "C"
        flag_str = f"[{flags}]" if flags else "    "

        print(f"  {i:>3}/{len(failures)} {flag_str} [{game[:25]:<25}] "
              f"[{old_v} -> {new_v}] {arrow:<14} {question[:55]}".encode('ascii','replace').decode('ascii'))

        results["questions"].append({
            "game": game, "question": question,
            "answer": out["answer"], "verdict": new_v,
            "judge_reason": judge.get("reason"),
            "top_pages": [c["page"] for c in out["top_chunks"]],
            "rerank_used": out["rerank_used"],
            "critique_changed": out["critique_changed"],
            "baseline_verdict": old_v,
        })
        save(results)
        done.add((game, question))

    # Aggregate report
    fixed = broke = same = 0
    by_game = {}
    for q in results["questions"]:
        if "verdict" not in q: continue
        old, new = q.get("baseline_verdict"), q["verdict"]
        old_pass = old in PASS
        new_pass = new in PASS
        bucket = "fixed" if (not old_pass and new_pass) else \
                  "broke" if (old_pass and not new_pass) else "same"
        if bucket == "fixed": fixed += 1
        elif bucket == "broke": broke += 1
        else: same += 1
        g = by_game.setdefault(q["game"], {"fixed": 0, "broke": 0, "same": 0})
        g[bucket] += 1

    print()
    print("=" * 70)
    print(f"FAILURES TARGETED: {len(failures)}")
    print(f"  fixed (was fail, now pass): {fixed}")
    print(f"  broke (was fail, still fail): {same - broke}  (still failing)")
    print(f"  newly broke (this run hit error): {broke}")
    print()
    print("Per-game:")
    for g, s in sorted(by_game.items()):
        total = s['fixed'] + s['broke'] + s['same']
        print(f"  {g:<35}  fixed={s['fixed']:>2}  still_fail={s['same']:>2}  total={total:>2}")
    print()
    pass_delta = fixed - broke
    new_total_pass = (sum(q.get("verdict") in PASS for q in results["questions"])
                      + sum(b.get("verdict") in PASS for b in
                            (q for q in (json.load(open(BASELINE_FILE, encoding="utf-8"))["questions"])
                             if (q["game"], q.get("question","")) not in
                             {(r["game"], r["question"]) for r in results["questions"]})))
    new_pass_rate = 100 * new_total_pass / 930
    print(f"Projected new pass rate (extrapolated to all 930): {new_pass_rate:.1f}%")


if __name__ == "__main__":
    main()
