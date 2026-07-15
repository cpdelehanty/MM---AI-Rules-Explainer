"""
Full library re-scan.

1. Back up DB
2. Clear chunks, games, processed_files tables (preserving cafe_games, deals, sessions, etc.)
3. Kick off process_rulebooks.py which will re-extract every PDF with OCR fallback

Preserves customer/business data. Only clears rulebook-derived tables.

Usage:
    python clear_and_rescan.py          # dry-run (shows what would be cleared)
    python clear_and_rescan.py --execute
"""
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime

DB_PATH = "game_library.db"
BACKUP_DIR = "backups"


def backup_db():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(BACKUP_DIR, f"game_library_{ts}_prerescan.db")
    shutil.copy(DB_PATH, dest)
    return dest


def main():
    execute = "--execute" in sys.argv
    mode = "EXECUTE" if execute else "DRY-RUN"
    print(f"=== {mode} ===\n")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Report current state
    for table in ("games", "chunks", "processed_files"):
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        n = cur.fetchone()[0]
        print(f"  {table}: {n} rows will be cleared")
    for table in ("cafe_games", "deals", "menu_items", "users", "active_sessions"):
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        n = cur.fetchone()[0]
        print(f"  {table}: {n} rows preserved (untouched)")
    print()

    if not execute:
        print("DRY-RUN. Re-run with --execute to actually clear + rescan.\n")
        return

    backup_path = backup_db()
    print(f"DB backed up to {backup_path}")

    cur.execute("DELETE FROM chunks")
    cur.execute("DELETE FROM processed_files")
    cur.execute("DELETE FROM games")
    conn.commit()
    conn.close()
    print("Cleared chunks, processed_files, games tables.\n")

    print("Kicking off process_rulebooks.py...")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    subprocess.run([sys.executable, "process_rulebooks.py"], env=env)


if __name__ == "__main__":
    main()
