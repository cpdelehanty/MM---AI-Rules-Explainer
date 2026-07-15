"""
SQLite layer for the rules corpus.

Four tables:
  - games              one row per game title (from process_rulebooks.py)
  - chunks             the RAG store; embeddings live in `embedding` as
                       raw float32 bytes (see _serialize_embedding)
  - processed_files    dedupe tracker for the ingestion pipeline
  - staff_requests     pending customer pings from the "get staff help"
                       button in app.py; a future admin dashboard polls it
"""

import json
import sqlite3

import numpy as np

DB_PATH = "game_library.db"

# Embedding storage format.
# Embeddings are stored in `chunks.embedding` as raw float32 little-endian
# bytes (numpy tobytes / frombuffer). ~5.5x smaller than JSON-encoded floats
# and keeps the DB under GitHub's 100MB per-file limit for our target
# ~500-game library.
EMBEDDING_DTYPE = np.float32


def _serialize_embedding(embedding):
    """Convert a list/array of floats to compact float32 bytes for BLOB storage."""
    return np.asarray(embedding, dtype=EMBEDDING_DTYPE).tobytes()


def _deserialize_embedding(blob):
    """Read a float32-bytes embedding back into a numpy array (or list for legacy JSON rows)."""
    # Backward compat: legacy rows may still be JSON strings if migration hasn't run.
    if isinstance(blob, str) or (isinstance(blob, (bytes, bytearray)) and blob[:1] == b"["):
        text = blob.decode("utf-8") if isinstance(blob, (bytes, bytearray)) else blob
        return np.array(json.loads(text), dtype=EMBEDDING_DTYPE)
    return np.frombuffer(blob, dtype=EMBEDDING_DTYPE)


def init_database():
    """Create the four tables + index if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL,
            total_pages INTEGER,
            total_chunks INTEGER,
            processed_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER NOT NULL,
            chunk_id INTEGER NOT NULL,
            page_number INTEGER NOT NULL,
            text TEXT NOT NULL,
            embedding BLOB NOT NULL,
            source_type TEXT DEFAULT 'rulebook',
            FOREIGN KEY (game_id) REFERENCES games(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS processed_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL UNIQUE,
            game_id INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            processed_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (game_id) REFERENCES games(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS staff_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visit_id TEXT,
            phone TEXT,
            table_number INTEGER,
            game_title TEXT,
            question TEXT,
            reason TEXT DEFAULT 'rules_question',
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            acknowledged_at TIMESTAMP
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_game_id ON chunks(game_id)")

    conn.commit()
    conn.close()


def game_exists(title):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM games WHERE title = ?", (title,))
    result = cursor.fetchone()
    conn.close()
    return result is not None


def file_already_processed(filename):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM processed_files WHERE filename = ?", (filename,))
    result = cursor.fetchone()
    conn.close()
    return result is not None


def add_game(title, filename, total_pages, chunks_with_embeddings, source_type='rulebook'):
    """
    Add a new game or append chunks to an existing one.

    If the game exists: appends chunks and updates page/chunk totals.
    If not: creates the row.
    Records the source PDF in `processed_files` for dedupe.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id, total_pages, total_chunks FROM games WHERE title = ?", (title,))
        existing = cursor.fetchone()

        if existing:
            game_id, old_pages, old_chunks = existing
            new_total_pages = old_pages + total_pages
            new_total_chunks = old_chunks + len(chunks_with_embeddings)
            cursor.execute("""
                UPDATE games
                SET total_pages = ?, total_chunks = ?, processed_date = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (new_total_pages, new_total_chunks, game_id))
            print(f"  ✅ Adding to existing game (now {new_total_chunks} total chunks)")
        else:
            cursor.execute("""
                INSERT INTO games (title, filename, total_pages, total_chunks)
                VALUES (?, ?, ?, ?)
            """, (title, filename, total_pages, len(chunks_with_embeddings)))
            game_id = cursor.lastrowid
            print(f"  ✅ Created new game entry")

        for chunk in chunks_with_embeddings:
            embedding_blob = _serialize_embedding(chunk['embedding'])
            cursor.execute("""
                INSERT INTO chunks (game_id, chunk_id, page_number, text, embedding, source_type)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (game_id, chunk['chunk_id'], chunk['page'], chunk['text'],
                  embedding_blob, source_type))

        cursor.execute("""
            INSERT OR IGNORE INTO processed_files (filename, game_id, source_type)
            VALUES (?, ?, ?)
        """, (filename, game_id, source_type))

        conn.commit()
        return game_id

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_all_games():
    """List all games in the library, sorted by title."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, title, total_pages, total_chunks, processed_date
        FROM games
        ORDER BY title
    """)
    games = cursor.fetchall()
    conn.close()
    return [
        {"id": g[0], "title": g[1], "total_pages": g[2],
         "total_chunks": g[3], "processed_date": g[4]}
        for g in games
    ]


def get_game_chunks(game_title):
    """All chunks for a game, ordered by chunk_id. Embedding is a numpy float32 array."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM games WHERE title = ?", (game_title,))
    game = cursor.fetchone()
    if not game:
        conn.close()
        return None

    game_id = game[0]
    cursor.execute("""
        SELECT chunk_id, page_number, text, embedding, source_type
        FROM chunks
        WHERE game_id = ?
        ORDER BY chunk_id
    """, (game_id,))
    chunks = cursor.fetchall()
    conn.close()

    return [
        {
            "chunk_id": c[0],
            "page": c[1],
            "text": c[2],
            "embedding": _deserialize_embedding(c[3]),
            "source_type": c[4] if len(c) > 4 else "rulebook",
        }
        for c in chunks
    ]


def delete_game(title):
    """Remove a game and its chunks. Returns True if a row was deleted."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM games WHERE title = ?", (title,))
        game = cursor.fetchone()
        if not game:
            return False
        game_id = game[0]
        cursor.execute("DELETE FROM chunks WHERE game_id = ?", (game_id,))
        cursor.execute("DELETE FROM processed_files WHERE game_id = ?", (game_id,))
        cursor.execute("DELETE FROM games WHERE id = ?", (game_id,))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_library_stats():
    """Row counts + totals across the library. Used by process_rulebooks.py."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM games")
    total_games = cursor.fetchone()[0]
    cursor.execute("SELECT SUM(total_pages) FROM games")
    total_pages = cursor.fetchone()[0] or 0
    cursor.execute("SELECT SUM(total_chunks) FROM games")
    total_chunks = cursor.fetchone()[0] or 0
    conn.close()
    return {
        "total_games": total_games,
        "total_pages": total_pages,
        "total_chunks": total_chunks,
    }
