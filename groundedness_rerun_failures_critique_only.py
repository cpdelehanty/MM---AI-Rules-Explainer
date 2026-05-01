"""
Re-run baseline failures with critique-only (no rerank).
Bora Bora's bigger chunks are already in the DB so they're in play.
Tells us how many of the 70 fixes we keep without rerank.
"""
import json, os, sys, time
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv(override=True)
from anthropic import Anthropic
import voyageai

from database import get_game_chunks
from rules_pipeline import run_pipeline
from groundedness_test import judge_answer, VOYAGE_DELAY

BASELINE_FILE = "groundedness_results_baseline.json"
RESULTS_FILE = "groundedness_results_failures_critique_only.json"
PASS = {"correct", "evasive_ok"}


def save(d):
    tmp = RESULTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    os.replace(tmp, RESULTS_FILE)


def main():
    with open(BASELINE_FILE, encoding="utf-8") as f:
        baseline_qs = json.load(f)["questions"]
    failures = [q for q in baseline_qs
                if "verdict" in q and q["verdict"] not in PASS]
    print(f"Baseline failures: {len(failures)} — running with CRITIQUE-ONLY")

    voyage_client = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])
    claude_client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    if os.path.exists(RESULTS_FILE):
        results = json.load(open(RESULTS_FILE, encoding="utf-8"))
        done = {(q["game"], q["question"]) for q in results.get("questions", [])
                if "verdict" in q}
    else:
        results = {"started_at": datetime.now(timezone.utc).isoformat(),
                   "mode": "failures_critique_only", "questions": []}
        save(results); done = set()

    last = 0.0; cache = {}
    for i, b in enumerate(failures, 1):
        g, q = b["game"], b["question"]
        if (g, q) in done: continue
        if g not in cache: cache[g] = get_game_chunks(g)
        if not cache[g]: continue
        elapsed = time.time() - last
        if elapsed < VOYAGE_DELAY: time.sleep(VOYAGE_DELAY - elapsed)
        try:
            out = run_pipeline(voyage_client, claude_client, q, g, cache[g],
                                use_rerank=False, use_critique=True,
                                cosine_top_k=5, rerank_top_k=5)
            last = time.time()
            judge = judge_answer(claude_client, g, q, out["answer"], out["top_chunks"])
        except Exception as e:
            print(f"  ! q{i} failed: {e}"); continue
        old_v = b.get("verdict", "?"); new_v = judge.get("verdict", "?")
        old_pass = old_v in PASS; new_pass = new_v in PASS
        arrow = "FIXED" if (not old_pass and new_pass) else (
            "BROKE" if (old_pass and not new_pass) else
            f"{old_v}->{new_v}" if old_v != new_v else "same")
        flag = "[C]" if out["critique_changed"] else "   "
        print(f"  {i:>3}/{len(failures)} {flag} [{g[:25]:<25}] [{old_v} -> {new_v}] {arrow}")
        results["questions"].append({"game": g, "question": q, "answer": out["answer"],
            "verdict": new_v, "judge_reason": judge.get("reason"),
            "rerank_used": False, "critique_changed": out["critique_changed"],
            "baseline_verdict": old_v})
        save(results); done.add((g, q))

    fixed = sum(1 for q in results["questions"]
                if q.get("baseline_verdict") not in PASS and q.get("verdict") in PASS)
    still_fail = sum(1 for q in results["questions"]
                     if q.get("baseline_verdict") not in PASS and q.get("verdict") not in PASS)
    critique_changed = sum(1 for q in results["questions"] if q.get("critique_changed"))

    print()
    print("="*60)
    print(f"Failures targeted: {len(failures)}")
    print(f"  fixed (now pass):    {fixed}")
    print(f"  still failing:       {still_fail}")
    print(f"  critique modified:   {critique_changed}")
    print()
    print(f"Combined run (rerank+critique) fixed: 70")
    print(f"Critique-only             fixed:      {fixed}")
    print(f"Lost from dropping rerank: {70 - fixed}")
    print()
    new_pass_count = 839 + fixed  # 839 baseline passes + new fixes
    print(f"Projected pass rate (critique-only): {100*new_pass_count/930:.1f}%")
    print(f"Projected pass rate (combined):       97.7%")
    print(f"Baseline pass rate:                   90.2%")


if __name__ == "__main__":
    main()
