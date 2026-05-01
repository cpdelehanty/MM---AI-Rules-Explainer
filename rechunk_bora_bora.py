"""
Re-chunk Bora Bora's rulebook with bigger chunks + bigger overlap.

The default 500-token chunks with 50-token overlap fragment Bora Bora's
rules across boundaries. Switching to 800-token / 200-overlap chunks
preserves more context per chunk and reduces "the answer is split
between adjacent chunks but neither has enough" failures.

Run once. Deletes the existing Bora Bora chunks/games rows and
re-processes the PDF using the larger chunk size.
"""
import os
import sqlite3
import sys
import time

from dotenv import load_dotenv

load_dotenv(override=True)

import voyageai
from process_rulebooks import (
    extract_text_from_pdf, chunk_text, create_embeddings,
    resolve_to_cafe_name,
)
from database import init_database, add_game, DB_PATH


PDF_PATH = "rulebooks/bora bora-rulebook.pdf"
NEW_CHUNK_SIZE = 800
NEW_OVERLAP = 200


def delete_existing(title):
    """Remove rows for `title` from games + chunks + processed_files."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id FROM games WHERE title = ?", (title,))
    row = cur.fetchone()
    if not row:
        print(f"  no existing game named {title!r}, nothing to delete")
        conn.close()
        return
    game_id = row[0]
    cur.execute("DELETE FROM chunks WHERE game_id = ?", (game_id,))
    cur.execute("DELETE FROM processed_files WHERE game_id = ?", (game_id,))
    cur.execute("DELETE FROM games WHERE id = ?", (game_id,))
    conn.commit()
    conn.close()
    print(f"  deleted existing rows for {title!r} (game_id={game_id})")


def main():
    if not os.path.exists(PDF_PATH):
        print(f"PDF not found: {PDF_PATH}")
        sys.exit(1)

    init_database()

    title = resolve_to_cafe_name("Bora Bora")
    print(f"Re-chunking {title!r} with size={NEW_CHUNK_SIZE} overlap={NEW_OVERLAP}")
    delete_existing(title)

    print("  extracting text...")
    pages = extract_text_from_pdf(PDF_PATH)
    total_pages = len(pages)

    print(f"  {total_pages} pages")
    print("  chunking...")
    chunks = chunk_text(pages, chunk_size=NEW_CHUNK_SIZE, overlap=NEW_OVERLAP)
    print(f"  {len(chunks)} chunks (was 27 with 500/50)")

    print("  embedding (rate-limited, may take a few minutes)...")
    voyage_client = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])
    chunks_with_embeddings = create_embeddings(chunks, voyage_client)

    print("  storing in database...")
    add_game(title, os.path.basename(PDF_PATH), total_pages,
             chunks_with_embeddings, source_type="rulebook")
    print("  done.")


if __name__ == "__main__":
    main()
