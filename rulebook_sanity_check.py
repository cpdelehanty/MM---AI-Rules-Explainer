"""
Rulebook sanity-check — second pass to catch broken PDFs that the golden-test
scan missed.

Three signals:
  1. Chunk-density scan (SQL only)     — chunks/page < 0.5 = probable truncation
  2. Random-chunk verification (Claude) — sample 1 chunk per game, ask if it
                                          looks like the right rulebook
  3. Missing-PDF cross-check           — cafe_games with no ingested rulebook

Output: rulebook_sanity_report.md — all signals combined per game.
"""
import json
import os
import random
import sqlite3
import time
from collections import defaultdict

from dotenv import load_dotenv

load_dotenv(override=True)
from anthropic import Anthropic
from config import CLAUDE_MODEL

DB_PATH = "game_library.db"
CACHE_FILE = "rulebook_sanity_cache.json"
OUTPUT_FILE = "rulebook_sanity_report.md"

# Reproducibility: seed the chunk sampling
random.seed(42)


# ---------------------------------------------------------------------------
# Signal 1: Chunk-density scan
# ---------------------------------------------------------------------------

def get_chunk_density():
    """Per-game chunk count vs page count. Low ratio = truncated ingestion."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT g.id, g.title, g.total_pages, COUNT(c.id) as chunks
        FROM games g
        LEFT JOIN chunks c ON c.game_id = g.id
        GROUP BY g.id
    """)
    results = []
    for gid, title, pages, chunks in cur.fetchall():
        pages = pages or 0
        density = chunks / pages if pages > 0 else 0
        results.append({
            "game_id": gid,
            "title": title,
            "pages": pages,
            "chunks": chunks,
            "density": density,
        })
    conn.close()
    return results


# ---------------------------------------------------------------------------
# Signal 2: Random chunk verification (Claude)
# ---------------------------------------------------------------------------

def sample_random_chunk(game_id):
    """Pull one random chunk's text for a game."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT text FROM chunks WHERE game_id = ? ORDER BY RANDOM() LIMIT 1", (game_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def verify_chunk_with_claude(client, game_name, excerpt):
    """Ask Claude to classify the excerpt."""
    # Truncate very long chunks to save tokens
    excerpt = excerpt[:2000] if excerpt else ""
    prompt = f"""You'll be shown a game name and one text excerpt from what's supposed to be that game's rulebook (as ingested into a RAG system).

Judge whether the excerpt actually is from the correct rulebook, or something is wrong.

GAME NAME: {game_name}

TEXT EXCERPT:
{excerpt}

Reply JSON only (no prose, no code fences):
{{"verdict": "looks_correct" | "wrong_game" | "foreign_language" | "garbage_text" | "too_short_or_thin" | "not_a_rulebook" | "unclear", "reason": "one short sentence", "detected_content_if_wrong": "what the text actually appears to be, or empty"}}

Definitions:
- looks_correct: reads like a plausible rulebook chunk for THIS game
- wrong_game: appears to be a rulebook, but for a DIFFERENT board game
- foreign_language: rulebook text is in a language other than English
- garbage_text: unreadable encoded characters, corrupted extraction
- too_short_or_thin: recognizable but only a fragment, headings, page numbers
- not_a_rulebook: text is something else entirely (novel, technical doc, RPG, etc.)
- unclear: can't tell, ambiguous, or the excerpt could plausibly be from the game"""

    for attempt in range(3):
        try:
            r = client.messages.create(
                model=CLAUDE_MODEL, max_tokens=250,
                messages=[{"role": "user", "content": prompt}],
            )
            text = r.content[0].text.strip()
            if text.startswith("```"):
                text = text.strip("`").lstrip("json\n").strip()
            s, e = text.find("{"), text.rfind("}")
            if s >= 0:
                return json.loads(text[s:e + 1])
        except Exception as ex:
            time.sleep(3 * (attempt + 1))
    return {"verdict": "unclear", "reason": "verification failed", "detected_content_if_wrong": ""}


# ---------------------------------------------------------------------------
# Signal 3: Missing PDFs
# ---------------------------------------------------------------------------

def get_missing_pdfs():
    """cafe_games with no ingested rulebook."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT cg.name FROM cafe_games cg
        LEFT JOIN games g ON LOWER(cg.name) = LOWER(g.title)
        WHERE g.id IS NULL
        ORDER BY cg.name
    """)
    result = [r[0] for r in cur.fetchall()]
    conn.close()
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("Missing ANTHROPIC_API_KEY")
        return
    client = Anthropic(api_key=key)

    # Signal 3 first — cheap, purely local
    missing = get_missing_pdfs()
    # Filter puzzles
    PUZZLE_PATTERNS = ["(500pc)", "(1000pc)", "Puzzle Co", "Buffalo Games", "Galison", "Ravensburger:"]
    missing_real = [m for m in missing if not any(p in m for p in PUZZLE_PATTERNS)]
    print(f"Missing PDFs (cafe games with no ingested rulebook): {len(missing_real)}")

    # Signal 1 — chunk density scan
    density_data = get_chunk_density()
    density_data.sort(key=lambda x: x["density"])
    thin = [d for d in density_data if 0 < d["density"] < 0.5]
    print(f"Thin-density games (chunks/page < 0.5): {len(thin)}")

    # Signal 2 — random chunk verification for every game with any chunks
    verify_targets = [d for d in density_data if d["chunks"] > 0]
    print(f"Games to verify with chunk sample: {len(verify_targets)}")

    # Resume support
    cache = {}
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
        print(f"Resuming: {len(cache)} games already verified")

    verified = {}
    for i, d in enumerate(verify_targets, 1):
        title = d["title"]
        if title in cache:
            verified[title] = cache[title]
            continue
        excerpt = sample_random_chunk(d["game_id"])
        if not excerpt or len(excerpt.strip()) < 20:
            verified[title] = {
                "verdict": "too_short_or_thin",
                "reason": "excerpt is empty or under 20 chars",
                "detected_content_if_wrong": "",
                "excerpt_preview": (excerpt or "")[:100],
            }
        else:
            result = verify_chunk_with_claude(client, title, excerpt)
            result["excerpt_preview"] = excerpt[:100]
            verified[title] = result

        cache[title] = verified[title]

        # Incremental save every 10
        if i % 10 == 0:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2, ensure_ascii=False)

        v = verified[title]["verdict"]
        mark = {
            "looks_correct": "V", "wrong_game": "X",
            "foreign_language": "L", "garbage_text": "G",
            "too_short_or_thin": "T", "not_a_rulebook": "R",
            "unclear": "?",
        }.get(v, ".")
        line = f"  {mark} {i:>3}/{len(verify_targets)}  {title[:32]:<32}  d={d['density']:.2f}  {v}"
        print(line.encode("ascii", "replace").decode("ascii"))

    # Final cache save
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)

    # Build report
    from collections import Counter
    counts = Counter(v["verdict"] for v in verified.values())

    # Games flagged for replacement
    flag_verdicts = {"wrong_game", "foreign_language", "garbage_text",
                     "too_short_or_thin", "not_a_rulebook"}
    flagged = {t: v for t, v in verified.items() if v["verdict"] in flag_verdicts}

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("# Rulebook Sanity Report\n\n")
        f.write(f"Scanned {len(verify_targets)} games with ingested chunks.\n\n")

        f.write("## Verdict distribution\n\n")
        for v, c in counts.most_common():
            pct = 100 * c / len(verify_targets)
            f.write(f"- **{v}**: {c} ({pct:.1f}%)\n")
        f.write("\n")

        f.write(f"## Games flagged for replacement ({len(flagged)})\n\n")
        by_verdict = defaultdict(list)
        for title, v in flagged.items():
            by_verdict[v["verdict"]].append((title, v))
        for verdict in ["wrong_game", "not_a_rulebook", "garbage_text",
                        "foreign_language", "too_short_or_thin"]:
            items = by_verdict.get(verdict, [])
            if not items:
                continue
            f.write(f"### {verdict} ({len(items)})\n\n")
            for title, v in sorted(items):
                # find density
                d = next((x for x in density_data if x["title"] == title), None)
                dstr = f"d={d['density']:.2f} chunks={d['chunks']} pages={d['pages']}" if d else ""
                f.write(f"- **{title}** — {v['reason']}  ({dstr})\n")
                if v.get("detected_content_if_wrong"):
                    f.write(f"    - Detected: *{v['detected_content_if_wrong']}*\n")
            f.write("\n")

        f.write(f"## Thin-density games (chunks/page < 0.5) ({len(thin)})\n\n")
        f.write("Some overlap with above; density-only flag = truncation risk.\n\n")
        for t in thin:
            v = verified.get(t["title"], {}).get("verdict", "?")
            f.write(f"- **{t['title']}** — {t['chunks']} chunks / {t['pages']} pages "
                    f"= {t['density']:.2f} (verify: {v})\n")
        f.write("\n")

        f.write(f"## Cafe games with no ingested PDF ({len(missing_real)})\n\n")
        for m in missing_real:
            f.write(f"- {m}\n")
        f.write("\n")

    print(f"\n{'=' * 76}")
    print(f"Sanity check complete")
    print(f"{'=' * 76}")
    print(f"Games flagged for replacement: {len(flagged)}")
    for v, c in counts.most_common():
        print(f"  {v:<24} {c}")
    print(f"\nMissing PDFs: {len(missing_real)}  (unfilterable puzzles excluded)")
    print(f"\nFull report: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
