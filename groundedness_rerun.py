"""
Re-run the groundedness test against the SAME questions used in a
prior run, but with the current pipeline (prompt + TOP_K + retrieval).

Reads question text from groundedness_results_baseline.json, runs each
through the current answer-with-citations + judge flow, writes to
groundedness_results.json.

Used to A/B test prompt and retrieval changes without question drift.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
from dotenv import load_dotenv

load_dotenv(override=True)

from anthropic import Anthropic
import voyageai

from database import get_game_chunks
from groundedness_test import (
    voyage_embed_query, top_chunks, answer_with_citations, judge_answer,
    save_results, load_results, write_summary_csv,
    VOYAGE_DELAY, RESULTS_FILE, SUMMARY_CSV,
)


BASELINE_FILE = "groundedness_results_baseline.json"


def main():
    if not os.path.exists(BASELINE_FILE):
        print(f"Missing baseline file: {BASELINE_FILE}")
        sys.exit(1)

    with open(BASELINE_FILE, encoding="utf-8") as f:
        baseline = json.load(f)
    baseline_qs = baseline["questions"]
    print(f"Baseline questions: {len(baseline_qs)}")

    voyage_client = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])
    claude_client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Fresh results — start over so the new run is clean
    if os.path.exists(RESULTS_FILE):
        os.rename(RESULTS_FILE, RESULTS_FILE + ".prev")
        print(f"(moved old {RESULTS_FILE} -> .prev)")

    results = {"runs": [{
        "started_at": datetime.now(timezone.utc).isoformat(),
        "mode": "rerun_against_baseline",
        "baseline_questions": len(baseline_qs),
    }], "questions": []}
    save_results(results)

    last_voyage = 0.0
    chunks_cache = {}

    for i, b in enumerate(baseline_qs, 1):
        if "question" not in b:
            continue
        game = b["game"]
        question = b["question"]

        if game not in chunks_cache:
            chunks_cache[game] = get_game_chunks(game)
        chunks = chunks_cache[game]
        if not chunks:
            print(f"  ! no chunks for {game!r}")
            continue

        # Voyage rate limit
        elapsed = time.time() - last_voyage
        if elapsed < VOYAGE_DELAY:
            time.sleep(VOYAGE_DELAY - elapsed)

        try:
            emb = voyage_embed_query(voyage_client, question)
            last_voyage = time.time()
            top = top_chunks(emb, chunks)
            answer = answer_with_citations(claude_client, question, game, top)
            judge = judge_answer(claude_client, game, question, answer, top)
        except Exception as e:
            print(f"  ! q{i} failed: {e}")
            results["questions"].append({
                "game": game, "question": question, "error": str(e),
                "asked_at": datetime.now(timezone.utc).isoformat(),
            })
            save_results(results)
            continue

        verdict = judge.get("verdict", "?")
        baseline_verdict = b.get("verdict", "?")
        delta = ""
        if verdict != baseline_verdict:
            delta = f"  [{baseline_verdict} -> {verdict}]"
        mark = {"correct": "✓", "partial": "≈", "wrong": "✗",
                "evasive_ok": "·", "evasive_lazy": "?"}.get(verdict, "·")
        print(f"  {mark} {i:>3}/{len(baseline_qs)} [{game[:25]:<25}] "
              f"[{verdict:<14}]{delta} {question[:60]}")

        results["questions"].append({
            "game": game,
            "question": question,
            "answer": answer,
            "verdict": verdict,
            "judge_reason": judge.get("reason"),
            "top_pages": [c["page"] for c in top],
            "baseline_verdict": baseline_verdict,
            "asked_at": datetime.now(timezone.utc).isoformat(),
        })
        save_results(results)

    write_summary_csv(results)

    # Compare aggregate
    from collections import Counter
    new_v = Counter(q.get("verdict", "?") for q in results["questions"])
    old_v = Counter(q.get("verdict", "?") for q in baseline_qs)

    def pct(c, n):
        return f"{100*c/max(n,1):.1f}%"

    n_new = sum(new_v.values())
    n_old = sum(old_v.values())
    print()
    print("=== A/B summary ===")
    print(f"{'verdict':<15} {'baseline':>10}  {'new':>10}")
    for v in ("correct", "evasive_ok", "partial", "evasive_lazy", "wrong"):
        print(f"{v:<15} {old_v[v]:>5} {pct(old_v[v], n_old):>5} "
              f"{new_v[v]:>5} {pct(new_v[v], n_new):>5}")
    pass_old = old_v["correct"] + old_v["evasive_ok"]
    pass_new = new_v["correct"] + new_v["evasive_ok"]
    print(f"\nPass rate: {pct(pass_old, n_old)} -> {pct(pass_new, n_new)}")


if __name__ == "__main__":
    main()
