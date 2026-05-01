"""
Sanity check — same 50 baseline passes, but critique ONLY (no rerank).
Isolates whether critique alone causes regressions.
"""
import json, os, random, sys, time
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv(override=True)
from anthropic import Anthropic
import voyageai

from database import get_game_chunks
from rules_pipeline import run_pipeline
from groundedness_test import judge_answer, VOYAGE_DELAY

BASELINE_FILE = "groundedness_results_baseline.json"
RESULTS_FILE = "groundedness_sanity_critique_only.json"
PASS = {"correct", "evasive_ok"}
SAMPLE_SIZE = 50
SEED = 42

def save(d):
    tmp = RESULTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    os.replace(tmp, RESULTS_FILE)

def main():
    with open(BASELINE_FILE, encoding="utf-8") as f:
        baseline_qs = json.load(f)["questions"]
    passes = [q for q in baseline_qs if q.get("verdict") in PASS]
    rng = random.Random(SEED)
    sample = rng.sample(passes, min(SAMPLE_SIZE, len(passes)))
    print(f"Sampling {len(sample)} passes (seed={SEED}) — CRITIQUE ONLY")

    voyage_client = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])
    claude_client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    if os.path.exists(RESULTS_FILE):
        results = json.load(open(RESULTS_FILE, encoding="utf-8"))
        done = {(q["game"], q["question"]) for q in results["questions"] if "verdict" in q}
    else:
        results = {"started_at": datetime.now(timezone.utc).isoformat(),
                   "mode": "critique_only", "questions": []}
        save(results); done = set()

    last = 0.0; cache = {}
    for i, b in enumerate(sample, 1):
        g, q = b["game"], b["question"]
        if (g, q) in done: continue
        if g not in cache: cache[g] = get_game_chunks(g)
        if not cache[g]: continue
        elapsed = time.time() - last
        if elapsed < VOYAGE_DELAY: time.sleep(VOYAGE_DELAY - elapsed)
        try:
            # use_rerank=False so retrieval is plain cosine top-K (same as baseline)
            out = run_pipeline(voyage_client, claude_client, q, g, cache[g],
                                use_rerank=False, use_critique=True,
                                cosine_top_k=5, rerank_top_k=5)
            last = time.time()
            judge = judge_answer(claude_client, g, q, out["answer"], out["top_chunks"])
        except Exception as e:
            print(f"  ! q{i} failed: {e}"); continue
        old_v = b.get("verdict", "?"); new_v = judge.get("verdict", "?")
        mark = "REGRESSION" if (old_v in PASS and new_v not in PASS) else (
            f"shifted ({old_v}->{new_v})" if old_v != new_v else "ok")
        critique_flag = "[C]" if out["critique_changed"] else "   "
        print(f"  {i:>3}/{len(sample)} {critique_flag} [{g[:25]:<25}] [{old_v} -> {new_v}] {mark}")
        results["questions"].append({"game": g, "question": q, "answer": out["answer"],
            "verdict": new_v, "judge_reason": judge.get("reason"),
            "rerank_used": False, "critique_changed": out["critique_changed"],
            "baseline_verdict": old_v})
        save(results); done.add((g, q))

    regs = [q for q in results["questions"]
            if q.get("baseline_verdict") in PASS and q.get("verdict") not in PASS]
    held = sum(1 for q in results["questions"]
               if q.get("baseline_verdict") in PASS and q.get("verdict") in PASS)
    critique_changed_count = sum(1 for q in results["questions"]
                                  if q.get("critique_changed"))
    print()
    print("="*60)
    print(f"Sample: {len(results['questions'])}")
    print(f"  held pass:           {held}")
    print(f"  REGRESSED to fail:   {len(regs)}")
    print(f"  critique changed:    {critique_changed_count}")
    if regs:
        print()
        for r in regs:
            print(f"  [{r['game']}] {r['question'][:70]}")
            print(f"    {r['baseline_verdict']} -> {r['verdict']}  changed={r.get('critique_changed')}")

if __name__ == "__main__": main()
