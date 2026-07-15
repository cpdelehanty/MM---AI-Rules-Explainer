"""
One-shot migration: convert JSON-string embeddings in chunks.embedding
to float32 binary bytes.

Reads legacy JSON rows and writes them back as compact bytes. Idempotent
— rows already in binary format are detected and skipped. After all rows
are converted, runs VACUUM to reclaim disk space.

Backup the DB first (backups/ dir). Migration is safe to run twice.
"""
import json
import sqlite3
import sys

import numpy as np

from database import DB_PATH, EMBEDDING_DTYPE


BATCH_SIZE = 500


def is_json_row(blob):
    """Legacy rows are JSON strings like '[0.123, ...]'; new rows are raw bytes."""
    if blob is None:
        return False
    if isinstance(blob, str):
        return True
    if isinstance(blob, (bytes, bytearray)):
        return blob[:1] == b"["
    return False


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM chunks")
    total = cur.fetchone()[0]
    print(f"Chunks in DB: {total}")

    # Count how many need conversion
    cur.execute("SELECT COUNT(*) FROM chunks WHERE SUBSTR(CAST(embedding AS TEXT), 1, 1) = '['")
    legacy = cur.fetchone()[0]
    print(f"Legacy JSON rows: {legacy}")
    print(f"Already binary:   {total - legacy}")

    if legacy == 0:
        print("Nothing to migrate.")
        conn.close()
        return

    print(f"\nMigrating {legacy} rows in batches of {BATCH_SIZE}...")

    cur.execute("SELECT id, embedding FROM chunks WHERE SUBSTR(CAST(embedding AS TEXT), 1, 1) = '[' ORDER BY id")
    rows = cur.fetchall()

    updates = []
    for i, (row_id, blob) in enumerate(rows, 1):
        text = blob if isinstance(blob, str) else blob.decode("utf-8")
        arr = np.array(json.loads(text), dtype=EMBEDDING_DTYPE)
        updates.append((arr.tobytes(), row_id))

        if len(updates) >= BATCH_SIZE:
            cur.executemany("UPDATE chunks SET embedding = ? WHERE id = ?", updates)
            conn.commit()
            print(f"  {i}/{legacy} migrated")
            updates.clear()

    if updates:
        cur.executemany("UPDATE chunks SET embedding = ? WHERE id = ?", updates)
        conn.commit()
        print(f"  {len(rows)}/{legacy} migrated")

    print("\nRunning VACUUM to reclaim space...")
    cur.execute("VACUUM")
    conn.commit()
    conn.close()

    import os
    size_mb = os.path.getsize(DB_PATH) / 1_000_000
    print(f"\nDB size after migration: {size_mb:.1f} MB")


if __name__ == "__main__":
    main()
