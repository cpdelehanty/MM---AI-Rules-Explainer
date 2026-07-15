"""
Find games where two source_type PDFs (rulebook / faq / errata / supplement)
contain near-identical content — a symptom of the same PDF being ingested
under two different filename conventions (e.g. Elder Sign has both
`elder sign-faq.pdf` and `elder sign-errata.pdf` with the same text).

Comparison: word-set Jaccard similarity on the concatenated chunk text of
each source_type. Threshold 0.8 = probably the same PDF.

Output: dupe_sources_report.md
"""
import sqlite3
from collections import defaultdict
from itertools import combinations

DB_PATH = "game_library.db"
OUTPUT_FILE = "dupe_sources_report.md"
SIMILARITY_THRESHOLD = 0.8


def word_set(text):
    tokens = [t.lower().strip(".,!?;:()[]\"'-—") for t in text.split()]
    return {t for t in tokens if 3 <= len(t) <= 20 and t.replace("-", "").replace("'", "").isalpha()}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Collect content per (game, source_type)
    cur.execute("""
        SELECT g.title, c.source_type, c.text
        FROM chunks c JOIN games g ON c.game_id = g.id
        ORDER BY g.title, c.source_type
    """)
    by_game_type = defaultdict(lambda: defaultdict(list))
    for title, st, text in cur.fetchall():
        by_game_type[title][st].append(text or "")

    conn.close()

    dupes = []
    for title, sources in by_game_type.items():
        types = list(sources.keys())
        if len(types) < 2:
            continue
        word_sets = {st: word_set(" ".join(sources[st])) for st in types}
        char_counts = {st: sum(len(t) for t in sources[st]) for st in types}
        for a, b in combinations(types, 2):
            sim = jaccard(word_sets[a], word_sets[b])
            if sim >= SIMILARITY_THRESHOLD:
                dupes.append({
                    "title": title, "a": a, "b": b,
                    "similarity": sim,
                    "a_chars": char_counts[a], "b_chars": char_counts[b],
                })

    dupes.sort(key=lambda d: (-d["similarity"], d["title"]))

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("# Duplicate Source Report\n\n")
        f.write(f"Games where two source_type PDFs contain near-identical content ")
        f.write(f"(Jaccard similarity ≥ {SIMILARITY_THRESHOLD}).\n\n")
        f.write(f"**{len(dupes)} duplicate pair(s) found.**\n\n")
        if dupes:
            f.write("| Game | Source A | Source B | Similarity | A chars | B chars |\n")
            f.write("|---|---|---|---|---|---|\n")
            for d in dupes:
                f.write(f"| {d['title']} | {d['a']} | {d['b']} | {d['similarity']*100:.0f}% | "
                        f"{d['a_chars']:,} | {d['b_chars']:,} |\n")

    print(f"Found {len(dupes)} dupe pair(s) across {len(by_game_type)} games.")
    if dupes:
        print()
        for d in dupes:
            print(f"  {d['title']:<40}  {d['a']:<10} = {d['b']:<10}  sim={d['similarity']*100:.0f}%")
    print(f"\nReport: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
