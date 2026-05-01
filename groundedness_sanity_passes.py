"""
Sanity check: run the new pipeline against a sample of previously-passing
questions to confirm it doesn't introduce regressions.

Samples 50 baseline passes (deterministic by seed), runs them through
rules_pipeline.run_pipeline, and reports any pass -> fail transitions.
"""
import json
import os
import random
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
RESULTS_FILE = "groundedness_sanity_v2.json"

PASS = {"correct", "evasive_ok"}
SAMPLE_SIZE = 50
SEED = 42


def save(data):
    tmp = RESULTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, RESULTS_FILE)


def main():
    with open(BASELINE_FILE, encoding="utf-8") as f:
        baseline_qs = json.load(f)["questions"]

    passes = [q for q in baseline_qs
              if "verdict" in q and q["verdict"] in PASS]
    print(f"Baseline passes: {len(passes)}")

    rng = random.Random(SEED)
    sample = rng.sample(passes, min(SAMPLE_SIZE, len(passes)))
    print(f"Sampling {len(sample)} passes for sanity check (seed={SEED})")

    voyage_client = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])
    claude_client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    if os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE, encoding="utf-8") as f:
            results = json.load(f)
        done = {(q["game"], q["question"]) for q in results.get("questions", [])
                if "verdict" in q}
    else:
        results = {"started_at": datetime.now(timezone.utc).isoformat(),
                   "sample_seed": SEED, "sample_size": len(sample),
                   "questions": []}
        save(results)
        done = set()

    last_voyage = 0.0
    chunks_cache = {}

    for i, b in enumerate(sample, 1):
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
            continue

        old_v = b.get("verdict", "?")
        new_v = judge.get("verdict", "?")
        old_pass = old_v in PASS
        new_pass = new_v in PASS

        if old_pass and not new_pass:
            mark = "REGRESSION"
        elif new_v != old_v:
            mark = f"shifted ({old_v}->{new_v})"
        else:
            mark = "ok"

        flags = ""
        if out["rerank_used"]: flags += "R"
        if out["critique_changed"]: flags += "C"
        flag_str = f"[{flags}]" if flags else "    "

        line = (f"  {i:>3}/{len(sample)} {flag_str} [{game[:25]:<25}] "
                f"[{old_v} -> {new_v}] {mark:<24} {question[:50]}")
        print(line.encode('ascii', 'replace').decode('ascii'))

        results["questions"].append({
            "game": game, "question": question,
            "answer": out["answer"], "verdict": new_v,
            "judge_reason": judge.get("reason"),
            "rerank_used": out["rerank_used"],
            "critique_changed": out["critique_changed"],
            "baseline_verdict": old_v,
        })
        save(results)
        done.add((game, question))

    # Final report
    regressions = []
    held = 0
    shifted_within_pass = 0
    for q in results["questions"]:
        if "verdict" not in q: continue
        old = q.get("baseline_verdict")
        new = q.get("verdict")
        if old in PASS and new not in PASS:
            regressions.append(q)
        elif old in PASS and new in PASS:
            held += 1
            if old != new:
                shifted_within_pass += 1

    total = len(results["questions"])
    print()
    print("=" * 70)
    print(f"Sample: {total}")
    print(f"  held pass:           {held}  ({shifted_within_pass} shifted within PASS)")
    print(f"  REGRESSED to fail:   {len(regressions)}")
    if regressions:
        print()
        print("Regressions:")
        for r in regressions:
            print(f"  [{r['game']}] {r['question'][:70]}")
            print(f"    {r['baseline_verdict']} -> {r['verdict']}")
            print(f"    judge: {r.get('judge_reason','')[:200]}")


if __name__ == "__main__":
    main()
