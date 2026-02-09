"""
Database layer for game library
Stores processed rulebooks in SQLite
"""

import sqlite3
import json
import os

DB_PATH = "game_library.db"

def init_database():
    """Initialize database schema"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Games table
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
    
    # Chunks table
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
    
    # Processed files table (tracks which specific files have been processed)
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
    
    # Create index for faster lookups
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_game_id ON chunks(game_id)
    """)
    
    conn.commit()
    conn.close()

def game_exists(title):
    """Check if game is already in database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM games WHERE title = ?", (title,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def file_already_processed(filename):
    """Check if a specific PDF file has already been processed"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM processed_files WHERE filename = ?", (filename,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def add_game(title, filename, total_pages, chunks_with_embeddings, source_type='rulebook'):
    """
    Add a new game or add chunks to existing game
    
    If game exists: adds chunks to existing game
    If game doesn't exist: creates new game entry
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Check if game already exists
        cursor.execute("SELECT id, total_pages, total_chunks FROM games WHERE title = ?", (title,))
        existing = cursor.fetchone()
        
        if existing:
            # Game exists - add to it
            game_id = existing[0]
            old_pages = existing[1]
            old_chunks = existing[2]
            
            # Update totals
            new_total_pages = old_pages + total_pages
            new_total_chunks = old_chunks + len(chunks_with_embeddings)
            
            cursor.execute("""
                UPDATE games 
                SET total_pages = ?, total_chunks = ?, processed_date = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (new_total_pages, new_total_chunks, game_id))
            
            print(f"  ✅ Adding to existing game (now {new_total_chunks} total chunks)")
        else:
            # New game - create entry
            cursor.execute("""
                INSERT INTO games (title, filename, total_pages, total_chunks)
                VALUES (?, ?, ?, ?)
            """, (title, filename, total_pages, len(chunks_with_embeddings)))
            
            game_id = cursor.lastrowid
            print(f"  ✅ Created new game entry")
        
        # Insert chunks with source type
        for chunk in chunks_with_embeddings:
            # Serialize embedding as JSON
            embedding_json = json.dumps(chunk['embedding'])
            
            cursor.execute("""
                INSERT INTO chunks (game_id, chunk_id, page_number, text, embedding, source_type)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (game_id, chunk['chunk_id'], chunk['page'], chunk['text'], embedding_json, source_type))
        
        # Record this file as processed
        cursor.execute("""
            INSERT OR IGNORE INTO processed_files (filename, game_id, source_type)
            VALUES (?, ?, ?)
        """, (filename, game_id, source_type))
        
        conn.commit()
        return game_id
    
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def get_all_games():
    """Get list of all games in library"""
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
        {
            "id": g[0],
            "title": g[1],
            "total_pages": g[2],
            "total_chunks": g[3],
            "processed_date": g[4]
        }
        for g in games
    ]

def get_game_chunks(game_title):
    """Get all chunks for a specific game"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get game ID
    cursor.execute("SELECT id FROM games WHERE title = ?", (game_title,))
    game = cursor.fetchone()
    
    if not game:
        conn.close()
        return None
    
    game_id = game[0]
    
    # Get chunks with source type
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
            "embedding": json.loads(c[3]),
            "source_type": c[4] if len(c) > 4 else "rulebook"  # Backward compatibility
        }
        for c in chunks
    ]

def delete_game(title):
    """Remove a game and all its chunks from database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Get game ID
        cursor.execute("SELECT id FROM games WHERE title = ?", (title,))
        game = cursor.fetchone()
        
        if game:
            game_id = game[0]
            
            # Delete chunks first (foreign key)
            cursor.execute("DELETE FROM chunks WHERE game_id = ?", (game_id,))
            
            # Delete game
            cursor.execute("DELETE FROM games WHERE id = ?", (game_id,))
            
            conn.commit()
            return True
        
        return False
    
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def get_library_stats():
    """Get statistics about the game library"""
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
        "total_chunks": total_chunks
    }
