"""
v2 A/B re-run: same baseline questions, enhanced rules pipeline.

Pipeline differences vs baseline:
  - Cosine retrieval pulls top 15 (vs 5)
  - Claude reranks 15 -> 8 most directly relevant
  - Self-critique pass after answering corrects unsupported claims

Reads questions from groundedness_results_baseline.json, runs each
through rules_pipeline.run_pipeline, judges, writes to
groundedness_results_v2.json.
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
from groundedness_test import (
    judge_answer, save_results, write_summary_csv, VOYAGE_DELAY,
)


BASELINE_FILE = "groundedness_results_baseline.json"
RESULTS_FILE = "groundedness_results_v2.json"


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

    # Resume support — if results exist, skip what's already graded
    if os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE, encoding="utf-8") as f:
            results = json.load(f)
        done_keys = {(q["game"], q["question"]) for q in results.get("questions", [])
                     if "verdict" in q}
        print(f"Resuming — {len(done_keys)} questions already done")
    else:
        results = {"runs": [{
            "started_at": datetime.now(timezone.utc).isoformat(),
            "mode": "v2_with_rerank_and_critique",
            "baseline_questions": len(baseline_qs),
        }], "questions": []}
        save_results_to(results)
        done_keys = set()

    last_voyage = 0.0
    chunks_cache = {}

    for i, b in enumerate(baseline_qs, 1):
        if "question" not in b:
            continue
        game = b["game"]
        question = b["question"]
        if (game, question) in done_keys:
            continue

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
                "asked_at": datetime.now(timezone.utc).isoformat(),
            })
            save_results_to(results)
            continue

        verdict = judge.get("verdict", "?")
        baseline_verdict = b.get("verdict", "?")
        delta = ""
        if verdict != baseline_verdict:
            delta = f"  [{baseline_verdict} -> {verdict}]"
        mark = {"correct": "v", "partial": "~", "wrong": "x",
                "evasive_ok": ".", "evasive_lazy": "?"}.get(verdict, ".")
        # ASCII-safe printing (Windows console)
        flags = ""
        if out["rerank_used"]: flags += "R"
        if out["critique_changed"]: flags += "C"
        if flags: flags = f"[{flags}] "
        print(f"  {mark} {i:>3}/{len(baseline_qs)} {flags}[{game[:25]:<25}] "
              f"[{verdict:<14}]{delta} {question[:55]}".encode('ascii', 'replace').decode('ascii'))

        results["questions"].append({
            "game": game,
            "question": question,
            "answer": out["answer"],
            "verdict": verdict,
            "judge_reason": judge.get("reason"),
            "top_pages": [c["page"] for c in out["top_chunks"]],
            "rerank_used": out["rerank_used"],
            "critique_changed": out["critique_changed"],
            "baseline_verdict": baseline_verdict,
            "asked_at": datetime.now(timezone.utc).isoformat(),
        })
        save_results_to(results)
        done_keys.add((game, question))

    # Summary
    from collections import Counter
    new_v = Counter(q.get("verdict", "?") for q in results["questions"])
    old_v = Counter(q.get("verdict", "?") for q in baseline_qs)
    n_new = sum(new_v.values())
    n_old = sum(old_v.values())

    def pct(c, n):
        return f"{100*c/max(n,1):.1f}%"

    print()
    print("=== A/B summary ===")
    print(f"{'verdict':<15} {'baseline':>10}  {'v2':>10}")
    for v in ("correct", "evasive_ok", "partial", "evasive_lazy", "wrong"):
        print(f"{v:<15} {old_v[v]:>5} {pct(old_v[v], n_old):>5}  "
              f"{new_v[v]:>5} {pct(new_v[v], n_new):>5}")
    pass_old = old_v["correct"] + old_v["evasive_ok"]
    pass_new = new_v["correct"] + new_v["evasive_ok"]
    print(f"\nPass rate: {pct(pass_old, n_old)} -> {pct(pass_new, n_new)}")


def save_results_to(data):
    tmp = RESULTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, RESULTS_FILE)


if __name__ == "__main__":
    main()
