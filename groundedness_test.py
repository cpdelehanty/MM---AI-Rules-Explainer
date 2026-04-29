"""
Groundedness test for the rules assistant.

For every cafe game with weight > 2.5 (heavy/medium-heavy strategy) and a
matching rulebook in the games table, generate 30 plausible rules
questions, run each through the actual rules-assistant pipeline (Voyage
embed -> top-K cosine -> Claude answer with citations), and grade each
answer with Claude-as-judge against the retrieved chunks.

Output: groundedness_results.json (full per-question records, resumable)
        groundedness_summary.csv  (per-game pass rate)

Designed to run unattended overnight. Saves after every question so a
crash doesn't lose progress; on restart it skips work already in the
results file.
"""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

import numpy as np
from dotenv import load_dotenv

load_dotenv(override=True)

try:
    from anthropic import Anthropic, APIStatusError
    import voyageai
except ImportError as e:
    print(f"Missing dependency: {e}")
    sys.exit(1)

from database import DB_PATH, get_game_chunks

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

WEIGHT_THRESHOLD = 2.5
QUESTIONS_PER_GAME = 30

# Voyage free tier ~3 RPM. Spacing query embeds at 22s/call gives margin.
VOYAGE_DELAY = 22.0

CLAUDE_MODEL = "claude-sonnet-4-20250514"
VOYAGE_MODEL = "voyage-3"
TOP_K = 5

RESULTS_FILE = "groundedness_results.json"
SUMMARY_CSV = "groundedness_summary.csv"


# --------------------------------------------------------------------------
# Pipeline (mirrors app.py)
# --------------------------------------------------------------------------

def cosine(a, b):
    a, b = np.array(a), np.array(b)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def voyage_embed_query(voyage_client, text):
    """One query embed with retry on transient errors."""
    last = None
    for attempt in range(4):
        try:
            r = voyage_client.embed(texts=[text], model=VOYAGE_MODEL,
                                     input_type="query")
            return r.embeddings[0]
        except Exception as e:
            last = e
            wait = 30 * (attempt + 1)
            print(f"    [voyage retry] {e} — sleep {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"voyage failed after retries: {last}")


def top_chunks(question_emb, chunks, k=TOP_K):
    scored = [(cosine(question_emb, c["embedding"]), c) for c in chunks]
    scored.sort(reverse=True, key=lambda x: x[0])
    return [c for _, c in scored[:k]]


def claude_call(client, prompt, max_tokens=1500):
    """Single Claude call with retry on rate-limit / overload."""
    last = None
    for attempt in range(4):
        try:
            r = client.messages.create(
                model=CLAUDE_MODEL, max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return r.content[0].text
        except APIStatusError as e:
            last = e
            if e.status_code in (429, 529):
                wait = 2 ** (attempt + 2)
                print(f"    [claude retry] HTTP {e.status_code} — sleep {wait}s")
                time.sleep(wait)
                continue
            raise
        except Exception as e:
            last = e
            wait = 5 * (attempt + 1)
            print(f"    [claude retry] {e} — sleep {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"claude failed after retries: {last}")


def build_context(top):
    parts = []
    for c in top:
        label = {
            "rulebook": "Rulebook", "faq": "FAQ",
            "errata": "Errata", "supplement": "Supplement",
        }.get(c.get("source_type", "rulebook"), "Rulebook")
        parts.append(f"[{label} - Page {c['page']}]\n{c['text']}")
    return "\n\n---\n\n".join(parts)


def answer_with_citations(claude, question, game_title, top):
    """Mirrors app.answer_question's prompt for the rules-only path."""
    context = build_context(top)
    prompt = f"""You are a helpful board game rules assistant at The Merry Meeple cafe.
Answer the customer's question based ONLY on the source documents provided below.

Provide a clear, direct answer to the specific question asked.

Rules for answering:
- Be friendly and conversational
- When citing information, include BOTH the source type AND page number
  Example: "The rulebook states each player draws 5 cards (Rulebook p. 3)"
- If information comes from multiple sources, cite all
- If the answer isn't in any of the provided sources, say "I don't see that
  information in the materials I have access to. Would you like me to
  request staff assistance?"
- If the question is unclear, ask ONE clarifying question
- Never make up rules that aren't in the source documents

SOURCE DOCUMENTS FOR {game_title.upper()}:
{context}

CUSTOMER QUESTION: {question}

YOUR ANSWER:"""
    return claude_call(claude, prompt, max_tokens=1500)


# --------------------------------------------------------------------------
# Question generation + judging
# --------------------------------------------------------------------------

def generate_questions(claude, game_name, weight, n=QUESTIONS_PER_GAME):
    """Ask Claude for N diverse rules questions players might genuinely ask."""
    prompt = f"""Generate {n} diverse, realistic rules questions a player might
ask about the board game "{game_name}" (BGG complexity {weight:.1f}/5).

Cover a mix of:
- Basic setup ("How do I set up the board?")
- Turn structure / actions
- Scoring / end-game / tiebreakers
- Specific component or card behaviors
- Edge cases and timing questions
- Cooperative / interaction rules
- Common rules confusions players actually hit

Each question should be a real question a customer might type into a
rules-assistant chatbot — natural, specific, not too long.

Return JSON ONLY in this exact shape (no prose, no code fences):
{{"questions": ["question 1", "question 2", ...]}}"""
    raw = claude_call(claude, prompt, max_tokens=3000)
    # Tolerant JSON extraction
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json\n").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return []
    try:
        data = json.loads(raw[start:end + 1])
        qs = data.get("questions") or []
        return [q.strip() for q in qs if isinstance(q, str) and q.strip()][:n]
    except json.JSONDecodeError:
        return []


def judge_answer(claude, game_name, question, answer, top):
    """
    Claude grades the answer against the retrieved chunks. Returns:
        {"verdict": "correct"|"partial"|"wrong"|"evasive_ok"|"evasive_lazy",
         "reason": "..."}
    """
    chunks_text = "\n\n---\n\n".join(
        f"[Page {c['page']}] {c['text']}" for c in top
    )
    prompt = f"""You are evaluating a board-game rules-assistant's answer against
the actual rulebook content it was given.

GAME: {game_name}
QUESTION: {question}

RETRIEVED RULEBOOK CHUNKS (the only source the assistant could see):
{chunks_text}

ASSISTANT'S ANSWER:
{answer}

Grade the answer with ONE of these verdicts:
- correct: answer matches the chunks; key facts right; cited a page
- partial: some right, some missing or imprecise; partly cites
- wrong: contradicts the chunks or invents content not in chunks
- evasive_ok: assistant said "I don't see that information" AND the
  chunks really do not contain the answer
- evasive_lazy: assistant said "I don't know" but the chunks DO contain
  the answer

Respond with JSON ONLY (no prose, no code fences):
{{"verdict": "...", "reason": "one short sentence"}}"""
    raw = claude_call(claude, prompt, max_tokens=300)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json\n").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1:
        return {"verdict": "parse_error", "reason": raw[:200]}
    try:
        return json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return {"verdict": "parse_error", "reason": raw[:200]}


# --------------------------------------------------------------------------
# Persistence (resumable)
# --------------------------------------------------------------------------

def load_results():
    if os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"runs": [], "questions": []}


def save_results(data):
    tmp = RESULTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, RESULTS_FILE)


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------

def load_qualifying_games():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT cg.name, cg.complexity, g.id, g.total_chunks
        FROM cafe_games cg
        JOIN games g ON g.title = cg.name
        WHERE cg.complexity > ? AND g.total_chunks > 0
        ORDER BY cg.complexity DESC
    """, (WEIGHT_THRESHOLD,))
    rows = cur.fetchall()
    conn.close()
    return [{"name": r[0], "weight": r[1], "game_id": r[2], "chunks": r[3]}
            for r in rows]


def main():
    voyage_key = os.environ.get("VOYAGE_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not voyage_key or not anthropic_key:
        print("Missing VOYAGE_API_KEY or ANTHROPIC_API_KEY in env")
        sys.exit(1)

    voyage_client = voyageai.Client(api_key=voyage_key)
    claude_client = Anthropic(api_key=anthropic_key)

    games = load_qualifying_games()
    print(f"Qualifying games: {len(games)} (weight > {WEIGHT_THRESHOLD}, "
          f"with rulebook chunks)")

    results = load_results()
    results["runs"].append({
        "started_at": datetime.now(timezone.utc).isoformat(),
        "questions_per_game": QUESTIONS_PER_GAME,
        "weight_threshold": WEIGHT_THRESHOLD,
    })
    save_results(results)

    done_keys = {(q["game"], q["question"]) for q in results["questions"]}
    print(f"Already completed: {len(done_keys)} questions (will skip)")

    last_voyage_call = 0.0

    for gi, game in enumerate(games, 1):
        print(f"\n[{gi}/{len(games)}] {game['name']} "
              f"(weight {game['weight']:.2f}, {game['chunks']} chunks)")

        # Skip generating if we already have all 30 questions for this game
        existing = [q for q in results["questions"] if q["game"] == game["name"]]
        if len(existing) >= QUESTIONS_PER_GAME:
            print(f"  already has {len(existing)} questions, skipping")
            continue

        # Generate the question set (one Claude call)
        try:
            questions = generate_questions(claude_client, game["name"],
                                           game["weight"], n=QUESTIONS_PER_GAME)
        except Exception as e:
            print(f"  ! question generation failed: {e}")
            continue

        if not questions:
            print(f"  ! got no questions back")
            continue

        # Load all chunks for this game once
        chunks = get_game_chunks(game["name"])
        if not chunks:
            print(f"  ! no chunks loadable for {game['name']!r}")
            continue

        for qi, q in enumerate(questions, 1):
            key = (game["name"], q)
            if key in done_keys:
                continue

            # Voyage rate limit: maintain min spacing
            elapsed = time.time() - last_voyage_call
            if elapsed < VOYAGE_DELAY:
                time.sleep(VOYAGE_DELAY - elapsed)

            try:
                emb = voyage_embed_query(voyage_client, q)
                last_voyage_call = time.time()
                top = top_chunks(emb, chunks)
                answer = answer_with_citations(
                    claude_client, q, game["name"], top
                )
                judge = judge_answer(
                    claude_client, game["name"], q, answer, top
                )
            except Exception as e:
                print(f"  ! q{qi} failed: {e}")
                results["questions"].append({
                    "game": game["name"], "question": q,
                    "error": str(e),
                    "asked_at": datetime.now(timezone.utc).isoformat(),
                })
                save_results(results)
                continue

            results["questions"].append({
                "game": game["name"],
                "question": q,
                "answer": answer,
                "verdict": judge.get("verdict"),
                "judge_reason": judge.get("reason"),
                "top_pages": [c["page"] for c in top],
                "asked_at": datetime.now(timezone.utc).isoformat(),
            })
            save_results(results)
            done_keys.add(key)

            verdict = judge.get("verdict", "?")
            mark = {"correct": "✓", "partial": "≈", "wrong": "✗",
                    "evasive_ok": "·", "evasive_lazy": "?"}.get(verdict, "·")
            print(f"  {mark} q{qi:>2}/{len(questions)} [{verdict:<14}] "
                  f"{q[:70]}")

    # Final summary CSV
    write_summary_csv(results)
    print()
    print(f"Done. Results: {RESULTS_FILE}, Summary: {SUMMARY_CSV}")


def write_summary_csv(results):
    import csv
    by_game = {}
    for q in results["questions"]:
        if "error" in q or "verdict" not in q:
            continue
        g = q["game"]
        by_game.setdefault(g, {"total": 0, "correct": 0, "partial": 0,
                               "wrong": 0, "evasive_ok": 0,
                               "evasive_lazy": 0, "parse_error": 0})
        by_game[g]["total"] += 1
        v = q.get("verdict") or "parse_error"
        if v in by_game[g]:
            by_game[g][v] += 1

    with open(SUMMARY_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["game", "total", "correct", "partial", "wrong",
                    "evasive_ok", "evasive_lazy", "parse_error",
                    "pass_rate"])
        for g, s in sorted(by_game.items()):
            denom = max(s["total"], 1)
            pass_rate = (s["correct"] + s["evasive_ok"]) / denom
            w.writerow([g, s["total"], s["correct"], s["partial"],
                        s["wrong"], s["evasive_ok"], s["evasive_lazy"],
                        s["parse_error"], f"{pass_rate:.2f}"])


if __name__ == "__main__":
    main()
