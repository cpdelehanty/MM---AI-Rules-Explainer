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
    
    # Menu items table (cached from Google Sheets)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS menu_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT UNIQUE NOT NULL,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            price TEXT,
            dietary_tags TEXT,
            available INTEGER DEFAULT 1,
            notes TEXT,
            last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Menu sync log
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS menu_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            items_synced INTEGER,
            status TEXT
        )
    """)

    # Deals table (synced from Google Sheets)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            display_text TEXT NOT NULL,
            discount_type TEXT NOT NULL,
            discount_value REAL,
            free_item_description TEXT,
            target_category TEXT,
            min_spend REAL DEFAULT 0,
            min_visit_count INTEGER DEFAULT 0,
            min_party_size INTEGER DEFAULT 1,
            first_visit_only INTEGER DEFAULT 0,
            time_of_day_start TEXT,
            time_of_day_end TEXT,
            days_of_week TEXT,
            active INTEGER DEFAULT 1,
            expiry_date TEXT,
            last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Deals sync log
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS deals_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deals_synced INTEGER,
            status TEXT
        )
    """)

    # Events table (synced from Google Sheets)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            date TEXT,
            time TEXT,
            game TEXT,
            display_text TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Events sync log
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            events_synced INTEGER,
            status TEXT
        )
    """)

    # Orders table (local record, also written to Google Sheets)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT UNIQUE NOT NULL,
            phone TEXT,
            visit_id TEXT,
            items TEXT NOT NULL,
            subtotal REAL NOT NULL,
            deals_applied TEXT,
            total REAL NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Auto deal rules (configurable spend-threshold offers)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS auto_deal_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            min_spend_threshold REAL NOT NULL,
            discount_percent REAL NOT NULL,
            max_discount REAL,
            display_template TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Auto deal rules sync log
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS auto_rules_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            rules_synced INTEGER,
            status TEXT
        )
    """)

    # Cart upsell rules (synced from Google Sheets)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cart_upsells (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upsell_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            requires_categories TEXT,
            excludes_categories TEXT,
            min_requires_count INTEGER DEFAULT 1,
            min_items INTEGER DEFAULT 0,
            max_items INTEGER DEFAULT 0,
            min_subtotal REAL DEFAULT 0,
            max_subtotal REAL DEFAULT 0,
            target_category TEXT NOT NULL,
            discount_percent REAL NOT NULL,
            message TEXT NOT NULL,
            suggested_items TEXT,
            priority INTEGER DEFAULT 10,
            active INTEGER DEFAULT 1,
            last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Cart upsells sync log
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cart_upsells_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            rules_synced INTEGER,
            status TEXT
        )
    """)

    # Security log (anomaly tracking for prompt injection attempts)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS security_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT,
            event_type TEXT NOT NULL,
            details TEXT,
            user_message TEXT,
            ai_response_snippet TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

def get_menu_items(category=None, available_only=True):
    """Get menu items from cache, optionally filtered by category"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = "SELECT * FROM menu_items"
    conditions = []
    params = []

    if available_only:
        conditions.append("available = 1")
    if category:
        conditions.append("category = ?")
        params.append(category)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY category, name"
    cursor.execute(query, params)
    items = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return items

def get_last_menu_sync():
    """Get timestamp of last successful menu sync (within last 4 hours)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT synced_at FROM menu_sync_log
        WHERE status = 'success'
          AND synced_at > datetime('now', '-4 hours')
        ORDER BY synced_at DESC LIMIT 1
    """)
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None


def get_active_deals():
    """Get all active deals"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM deals WHERE active = 1")
    deals = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return deals


def get_last_deals_sync():
    """Get timestamp of last successful deals sync (today only)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT synced_at FROM deals_sync_log
        WHERE status = 'success'
          AND date(synced_at) = date('now')
        ORDER BY synced_at DESC LIMIT 1
    """)
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None


def get_active_events():
    """Get all active events"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM events WHERE active = 1")
    events = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return events


def get_last_events_sync():
    """Get timestamp of last successful events sync (today only)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT synced_at FROM events_sync_log
        WHERE status = 'success'
          AND date(synced_at) = date('now')
        ORDER BY synced_at DESC LIMIT 1
    """)
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None


def get_auto_deal_rules():
    """Get all active auto-deal rules"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM auto_deal_rules WHERE active = 1")
    rules = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rules


def get_cart_upsell_rules():
    """Get all active cart upsell rules, ordered by priority"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM cart_upsells WHERE active = 1 ORDER BY priority ASC")
    rules = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rules


def get_last_cart_upsells_sync():
    """Get timestamp of last successful cart upsells sync (today only)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT synced_at FROM cart_upsells_sync_log
        WHERE status = 'success'
          AND date(synced_at) = date('now')
        ORDER BY synced_at DESC LIMIT 1
    """)
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None


def get_last_auto_rules_sync():
    """Get timestamp of last successful auto rules sync (today only)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT synced_at FROM auto_rules_sync_log
        WHERE status = 'success'
          AND date(synced_at) = date('now')
        ORDER BY synced_at DESC LIMIT 1
    """)
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None


def save_order(order_id, phone, visit_id, items_json, subtotal, deals_json, total):
    """Save an order to local database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO orders (order_id, phone, visit_id, items, subtotal, deals_applied, total, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
    """, (order_id, phone, visit_id, items_json, subtotal, deals_json, total))
    conn.commit()
    conn.close()


def log_security_event(phone, event_type, details, user_message=None, ai_response_snippet=None):
    """Log a security event (prompt injection attempt, invalid deal, etc.)"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO security_log (phone, event_type, details, user_message, ai_response_snippet)
        VALUES (?, ?, ?, ?, ?)
    """, (phone, event_type, details, user_message, ai_response_snippet))
    conn.commit()
    conn.close()
