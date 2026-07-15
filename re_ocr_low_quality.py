"""
Targeted re-extraction pass on pages that scored < 0.3 in the main rescan.

For each candidate page:
  1. Get current text from DB (concat of all chunks for that page)
  2. Re-run pypdf on the page
  3. Re-run rotation-aware OCR (ocr_fallback now tries 90/180/270 on garbage)
  4. Pick best via quality_score
  5. If new_quality - current_quality >= IMPROVEMENT_THRESHOLD, mark for replacement

Two modes:
  --dry-run  : report which pages improve, don't touch DB
  --apply    : actually replace chunks + re-embed (uses Voyage, rate-limited)
"""
import os
import sqlite3
import sys
import time
from collections import defaultdict

from dotenv import load_dotenv
from pypdf import PdfReader

from process_rulebooks import chunk_text, quality_score, CHUNK_SIZE, CHUNK_OVERLAP
from ocr_fallback import ocr_pages_from_pdf

load_dotenv()

DB_PATH = "game_library.db"
RULEBOOKS_DIR = "rulebooks"
QUALITY_THRESHOLD = 0.3           # pages below this get re-processed
IMPROVEMENT_THRESHOLD = 0.20       # only replace if new quality beats old by this margin
RATE_LIMIT_DELAY = 25              # Voyage free tier


def get_candidates(cur):
    """Return list of (game_id, title, source_type, page_number, filename, current_text, current_quality)"""
    cur.execute("""
        SELECT c.game_id, g.title, c.source_type, c.page_number, c.text, pf.filename
        FROM chunks c
        JOIN games g ON g.id = c.game_id
        LEFT JOIN processed_files pf ON pf.game_id = c.game_id AND pf.source_type = c.source_type
        ORDER BY c.game_id, c.source_type, c.page_number
    """)
    pages = defaultdict(lambda: {"texts": [], "title": "", "filename": ""})
    for gid, title, st, pn, text, fn in cur.fetchall():
        key = (gid, st, pn)
        pages[key]["texts"].append(text or "")
        pages[key]["title"] = title
        pages[key]["filename"] = fn

    candidates = []
    for (gid, st, pn), info in pages.items():
        text = "\n".join(info["texts"])
        q = quality_score(text)
        if q < QUALITY_THRESHOLD and info["filename"]:
            candidates.append({
                "game_id": gid, "title": info["title"], "source_type": st,
                "page_number": pn, "filename": info["filename"],
                "current_text": text, "current_quality": q,
            })
    return candidates


def try_pypdf(pdf_path, page_num):
    try:
        reader = PdfReader(pdf_path)
        if page_num <= len(reader.pages):
            return (reader.pages[page_num - 1].extract_text() or "").strip()
    except Exception as e:
        print(f"    ! pypdf failed on p{page_num}: {e}")
    return ""


def process_candidate(c):
    """Re-extract this page and return (best_text, best_quality, source_used)."""
    pdf_path = os.path.join(RULEBOOKS_DIR, c["filename"])
    if not os.path.exists(pdf_path):
        return None, 0.0, "missing"

    # pypdf
    py_text = try_pypdf(pdf_path, c["page_number"])
    py_q = quality_score(py_text)

    # rotation-aware OCR
    ocr_results = ocr_pages_from_pdf(pdf_path, [c["page_number"]])
    ocr_text = ocr_results.get(c["page_number"], "")
    ocr_q = quality_score(ocr_text)

    if py_q >= ocr_q:
        return py_text, py_q, "pypdf"
    return ocr_text, ocr_q, "ocr"


def dry_run(candidates):
    print(f"\nDry-running {len(candidates)} candidate pages...\n")
    improved = []
    unchanged = []
    for i, c in enumerate(candidates, 1):
        title = c["title"][:35]
        prefix = f"[{i:>2}/{len(candidates)}] {title:<35} p{c['page_number']:>3}"
        best_text, best_q, source = process_candidate(c)
        gain = best_q - c["current_quality"]
        marker = "✓" if gain >= IMPROVEMENT_THRESHOLD else "·"
        print(f"  {marker} {prefix}  Q: {c['current_quality']:.2f} -> {best_q:.2f}  ({source}, {len(best_text)}c, gain={gain:+.2f})")
        entry = {**c, "best_text": best_text, "best_quality": best_q, "source": source, "gain": gain}
        if gain >= IMPROVEMENT_THRESHOLD:
            improved.append(entry)
        else:
            unchanged.append(entry)

    print(f"\n=== Summary ===")
    print(f"  Would replace: {len(improved)} page(s)")
    print(f"  No change:     {len(unchanged)} page(s)")
    if improved:
        total_new_chunks = 0
        for e in improved:
            # Estimate chunk count for this text
            est_chunks = max(1, (len(e["best_text"]) // 2000) + 1)
            total_new_chunks += est_chunks
        est_embed_time = (total_new_chunks / 10) * RATE_LIMIT_DELAY
        print(f"  Est. new chunks to embed: ~{total_new_chunks}")
        print(f"  Est. embedding time: ~{est_embed_time/60:.1f} min")
    return improved, unchanged


def apply_replacements(improved, cur, conn, voyage_client):
    import voyageai
    from config import VOYAGE_MODEL

    print(f"\nApplying replacements for {len(improved)} page(s)...\n")

    # Build all new chunks first so we can batch embed
    all_new = []  # [{game_id, source_type, page_number, text}]
    for e in improved:
        pages = [{"page": e["page_number"], "text": e["best_text"]}]
        new_chunks = chunk_text(pages)
        for nc in new_chunks:
            all_new.append({
                "game_id": e["game_id"],
                "source_type": e["source_type"],
                "page_number": e["page_number"],
                "text": nc["text"],
            })

    print(f"  Total new chunks to embed: {len(all_new)}")

    # Batch embed
    batch_size = 10
    for i in range(0, len(all_new), batch_size):
        batch = all_new[i:i+batch_size]
        texts = [c["text"] for c in batch]
        print(f"  Embedding batch {i//batch_size + 1}/{(len(all_new)+batch_size-1)//batch_size} ({len(batch)} chunks)...")
        result = voyage_client.embed(texts=texts, model=VOYAGE_MODEL, input_type="document")
        for c, emb in zip(batch, result.embeddings):
            c["embedding"] = emb
        if i + batch_size < len(all_new):
            print(f"  ⏳ Waiting {RATE_LIMIT_DELAY}s (rate limit)...")
            time.sleep(RATE_LIMIT_DELAY)

    # For each improved page, delete existing chunks and insert new
    import json
    import numpy as np
    print(f"\n  Committing to DB...")
    for e in improved:
        cur.execute("""
            DELETE FROM chunks
            WHERE game_id = ? AND source_type = ? AND page_number = ?
        """, (e["game_id"], e["source_type"], e["page_number"]))

    # Insert all new chunks
    for c in all_new:
        emb_json = json.dumps(list(c["embedding"]))
        # Fetch a new chunk_id per game
        cur.execute("""
            SELECT COALESCE(MAX(chunk_id), -1) + 1 FROM chunks WHERE game_id = ?
        """, (c["game_id"],))
        new_chunk_id = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO chunks (game_id, chunk_id, page_number, text, embedding, source_type)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (c["game_id"], new_chunk_id, c["page_number"], c["text"], emb_json, c["source_type"]))
    conn.commit()
    print(f"  ✅ Replaced {len(improved)} page(s), inserted {len(all_new)} new chunk(s)")


def main():
    apply = "--apply" in sys.argv

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    candidates = get_candidates(cur)
    print(f"Found {len(candidates)} candidate page(s) with quality < {QUALITY_THRESHOLD}")

    improved, unchanged = dry_run(candidates)

    if apply and improved:
        # Backup DB
        import shutil
        from datetime import datetime
        os.makedirs("backups", exist_ok=True)
        backup = f"backups/game_library_{datetime.now().strftime('%Y%m%d_%H%M%S')}_pre_reocr.db"
        shutil.copy(DB_PATH, backup)
        print(f"\nDB backed up to: {backup}")

        import voyageai
        api_key = os.environ.get("VOYAGE_API_KEY")
        voyage_client = voyageai.Client(api_key=api_key)
        apply_replacements(improved, cur, conn, voyage_client)
    elif not apply:
        print(f"\n[dry-run] Rerun with --apply to commit changes")

    conn.close()


if __name__ == "__main__":
    main()
