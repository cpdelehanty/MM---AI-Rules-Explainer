"""
Identify games that warrant manual review, using multiple heuristics beyond
just the quality score.

Red flags:
  - quality < 0.85 (post-fix, healthy games score 0.95+)
  - very short content (< 500 total chars) - likely truncated ingestion
  - high non-alpha ratio (lots of digits/symbols in body content)
  - high mixed% in extraction (>30% of pages went to fallback / mixed) -
    indicates a wonky PDF where neither extractor is clearly right
  - very few chunks relative to page count (< 1 chunk/page average)
"""
import os
import sqlite3
from collections import defaultdict

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


def alpha_ratio(text):
    """Fraction of non-whitespace chars that are alphabetic."""
    non_ws = [c for c in text if not c.isspace()]
    if not non_ws:
        return 0.0
    return sum(1 for c in non_ws if c.isalpha()) / len(non_ws)


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        SELECT g.id, g.title, g.filename, g.total_pages, g.total_chunks
        FROM games g ORDER BY g.title
    """)
    games = cur.fetchall()

    problems = []
    for gid, title, filename, total_pages, total_chunks in games:
        cur.execute("""
            SELECT page_number, source_type, text FROM chunks
            WHERE game_id = ? ORDER BY page_number
        """, (gid,))
        chunks_by_source = defaultdict(list)
        chunks_by_page = defaultdict(list)
        all_text_parts = []
        for pn, st, text in cur.fetchall():
            text = text or ""
            chunks_by_source[st].append(text)
            chunks_by_page[pn].append(text)
            all_text_parts.append(text)
        all_text = "\n".join(all_text_parts)

        if not all_text.strip():
            problems.append({
                "title": title, "flags": ["EMPTY (no chunks)"],
                "quality": 0.0, "chars": 0, "pages": total_pages,
                "chunks": total_chunks,
            })
            continue

        # Quality
        # Compute per-page quality then average (page-weighted)
        page_qualities = []
        for pn, parts in chunks_by_page.items():
            page_qualities.append(quality_score("\n".join(parts)))
        avg_q = sum(page_qualities) / len(page_qualities) if page_qualities else 0.0

        # Extractor comparison — how often did pypdf and DB agree?
        mixed_count = 0
        if filename:
            pdf_path = os.path.join(RULEBOOKS_DIR, filename)
            if os.path.exists(pdf_path):
                try:
                    reader = PdfReader(pdf_path)
                    for pn, parts in chunks_by_page.items():
                        try:
                            py_text = (reader.pages[pn - 1].extract_text() or "").strip()
                            db_text = "\n".join(parts)
                            j = jaccard(word_set(py_text), word_set(db_text))
                            if 0.2 < j < 0.5:
                                mixed_count += 1
                        except Exception:
                            pass
                except Exception:
                    pass
        mixed_pct = 100 * mixed_count / max(len(chunks_by_page), 1)

        alpha = alpha_ratio(all_text)
        chunks_per_page = total_chunks / max(total_pages, 1)

        flags = []
        if avg_q < 0.85:
            flags.append(f"low quality {avg_q:.2f}")
        if len(all_text) < 500:
            flags.append(f"very short ({len(all_text)}c)")
        if alpha < 0.65:
            flags.append(f"high non-alpha {(1-alpha)*100:.0f}%")
        if mixed_pct > 30:
            flags.append(f"messy PDF (mixed on {mixed_pct:.0f}% of pages)")
        if chunks_per_page < 0.7 and total_pages > 3:
            flags.append(f"thin ({chunks_per_page:.1f} chunks/page)")

        if flags:
            problems.append({
                "title": title, "flags": flags, "quality": avg_q,
                "chars": len(all_text), "pages": total_pages,
                "chunks": total_chunks, "alpha": alpha, "mixed_pct": mixed_pct,
            })

    conn.close()

    problems.sort(key=lambda p: p["quality"])
    print(f"=== {len(problems)} games with at least one red flag ===\n")
    for p in problems:
        title = p["title"][:36]
        flags_str = ", ".join(p["flags"])
        print(f"  {title:<36}  Q={p['quality']:.2f}  {p['pages']}p/{p['chunks']}c  →  {flags_str}")


if __name__ == "__main__":
    main()
