"""
Full per-game extraction breakdown.
For each processed game, check every page: which extractor won (pypdf vs OCR
vs mixed), and what quality score the stored text earned.
"""
import os
import sqlite3
import sys
from collections import Counter, defaultdict

from pypdf import PdfReader
from process_rulebooks import quality_score

DB_PATH = "game_library.db"
RULEBOOKS_DIR = "rulebooks"


def word_set(text):
    tokens = [t.lower().strip(".,!?;:()[]\"'-—") for t in text.split()]
    return {t for t in tokens if 3 <= len(t) <= 20 and t.replace("-", "").replace("'", "").isalpha()}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def classify(overlap, py_len, db_len):
    """
    Classify: pypdf won, OCR won, or mixed/unclear.
    Special case: if pypdf produced almost nothing but DB has content, OCR won.
    """
    if py_len < 20 and db_len > 100:
        return "OCR"  # pypdf failed entirely
    if overlap >= 0.5:
        return "pypdf"
    if overlap <= 0.2:
        return "OCR"
    return "mixed"


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT g.id, g.title, g.filename, g.total_pages, g.total_chunks
        FROM games g ORDER BY g.title
    """)
    games = cur.fetchall()

    print(f"Analyzing {len(games)} processed games...\n", file=sys.stderr)

    results = []
    for i, (gid, title, filename, total_pages, total_chunks) in enumerate(games, 1):
        pdf_path = os.path.join(RULEBOOKS_DIR, filename) if filename else None
        if not pdf_path or not os.path.exists(pdf_path):
            results.append({"title": title, "note": "PDF missing"})
            continue

        # Get all DB chunks, group by page
        cur.execute("SELECT page_number, text FROM chunks WHERE game_id=? ORDER BY page_number", (gid,))
        db_by_page = defaultdict(list)
        for pn, text in cur.fetchall():
            db_by_page[pn].append(text or "")
        pages_with_content = sorted(db_by_page.keys())

        try:
            reader = PdfReader(pdf_path)
        except Exception as e:
            results.append({"title": title, "note": f"pypdf error: {e}"})
            continue

        classifications = []
        quality_scores = []
        for pn in pages_with_content:
            db_text = "\n".join(db_by_page[pn])
            try:
                py_text = (reader.pages[pn - 1].extract_text() or "").strip()
            except Exception:
                py_text = ""
            overlap = jaccard(word_set(py_text), word_set(db_text))
            classifications.append(classify(overlap, len(py_text), len(db_text)))
            quality_scores.append(quality_score(db_text))

        if not classifications:
            results.append({"title": title, "note": "no chunks"})
            continue

        counts = Counter(classifications)
        total = len(classifications)
        pct_pypdf = 100 * counts["pypdf"] / total
        pct_ocr = 100 * counts["OCR"] / total
        pct_mixed = 100 * counts["mixed"] / total
        avg_q = sum(quality_scores) / len(quality_scores)

        results.append({
            "title": title,
            "n_pages": total,
            "pct_pypdf": pct_pypdf,
            "pct_ocr": pct_ocr,
            "pct_mixed": pct_mixed,
            "avg_q": avg_q,
        })

        if i % 20 == 0:
            print(f"  ... analyzed {i}/{len(games)}", file=sys.stderr)

    conn.close()

    # Sort alphabetically for the main table
    good = [r for r in results if "avg_q" in r]
    bad = [r for r in results if "avg_q" not in r]

    # Print header
    print("\n=== Per-game extraction breakdown ===\n")
    print(f"{'Game':<40} {'Pages':>5}  {'pypdf%':>6}  {'OCR%':>5}  {'mix%':>5}  {'Quality':>7}")
    print(f"{'-'*40} {'-'*5}  {'-'*6}  {'-'*5}  {'-'*5}  {'-'*7}")
    for r in sorted(good, key=lambda x: x["title"]):
        print(f"{r['title'][:40]:<40} {r['n_pages']:>5}  "
              f"{r['pct_pypdf']:>5.0f}%  {r['pct_ocr']:>4.0f}%  {r['pct_mixed']:>4.0f}%  "
              f"{r['avg_q']:>7.2f}")

    if bad:
        print(f"\n=== Excluded ({len(bad)}) ===")
        for r in bad:
            print(f"  {r['title']}: {r.get('note', '?')}")

    # Overall aggregate
    print(f"\n=== Overall ({len(good)} games) ===")
    if good:
        total_pages = sum(r['n_pages'] for r in good)
        total_pypdf = sum(r['n_pages'] * r['pct_pypdf'] / 100 for r in good)
        total_ocr = sum(r['n_pages'] * r['pct_ocr'] / 100 for r in good)
        total_mixed = sum(r['n_pages'] * r['pct_mixed'] / 100 for r in good)
        avg_q_overall = sum(r['avg_q'] * r['n_pages'] for r in good) / total_pages
        print(f"Pages: {total_pages} total")
        print(f"  pypdf won: {total_pypdf:.0f} ({100*total_pypdf/total_pages:.0f}%)")
        print(f"  OCR won:   {total_ocr:.0f} ({100*total_ocr/total_pages:.0f}%)")
        print(f"  mixed:     {total_mixed:.0f} ({100*total_mixed/total_pages:.0f}%)")
        print(f"Average quality (page-weighted): {avg_q_overall:.2f}")


if __name__ == "__main__":
    main()
