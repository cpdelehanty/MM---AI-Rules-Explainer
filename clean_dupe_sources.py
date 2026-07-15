"""
Clean up the 4 confirmed dupe source_type pairs.

For each pair, delete the FAQ copy (keep errata) since:
- Both contain identical content (verified via dupe detection)
- FAQ and errata are meant to be different documents; if identical, one was misnamed
- Keeping errata (the more specific label) is safer

Actions per game:
- Delete chunks with source_type=faq
- Delete processed_files row for the faq
- Move the faq PDF to rulebooks_removed/ prefixed with duplicate__

DB backed up first.
"""
import os
import shutil
import sqlite3
from datetime import datetime

DB_PATH = "game_library.db"
BACKUP_DIR = "backups"
RULEBOOKS_DIR = "rulebooks"
REMOVED_DIR = "rulebooks_removed"

# From find_dupe_sources.py output
DUPE_GAMES = [
    "Cosmic Encounter",
    "Elder Sign",
    "Everdell",
    "Ark Nova",
]
DELETE_SOURCE_TYPE = "faq"  # keep errata, delete the faq duplicate


def backup_db():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(BACKUP_DIR, f"game_library_{ts}_predupeclean.db")
    shutil.copy(DB_PATH, dest)
    return dest


def main():
    backup_path = backup_db()
    print(f"DB backed up to: {backup_path}\n")
    os.makedirs(REMOVED_DIR, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    total_purged = 0

    for title in DUPE_GAMES:
        cur.execute("SELECT id FROM games WHERE title = ?", (title,))
        row = cur.fetchone()
        if not row:
            print(f"  ! {title}: game not found")
            continue
        gid = row[0]

        # Get faq processed_files rows for the filename
        cur.execute("""
            SELECT id, filename FROM processed_files
            WHERE game_id = ? AND source_type = ?
        """, (gid, DELETE_SOURCE_TYPE))
        pf_rows = cur.fetchall()

        # Delete chunks
        cur.execute("""
            DELETE FROM chunks WHERE game_id = ? AND source_type = ?
        """, (gid, DELETE_SOURCE_TYPE))
        chunks_deleted = cur.rowcount

        # Delete processed_files rows
        cur.execute("""
            DELETE FROM processed_files WHERE game_id = ? AND source_type = ?
        """, (gid, DELETE_SOURCE_TYPE))
        files_deleted = cur.rowcount

        # Move PDFs to rulebooks_removed/
        moved = []
        for _, fn in pf_rows:
            src = os.path.join(RULEBOOKS_DIR, fn)
            if os.path.exists(src):
                dest_name = f"duplicate__faq__{fn}"
                dest = os.path.join(REMOVED_DIR, dest_name)
                shutil.move(src, dest)
                moved.append(dest_name)

        total_purged += 1
        line = f"  X {title:<30}  chunks={chunks_deleted:>3}  files_row={files_deleted}  pdf_moved={len(moved)}"
        print(line.encode("ascii", "replace").decode("ascii"))

    conn.commit()
    conn.close()
    print(f"\nPurged {total_purged}/{len(DUPE_GAMES)} dupe pairs (kept errata, dropped faq).")


if __name__ == "__main__":
    main()
