"""
Database layer for recommendation system
Extends game_library.db with users, preferences, ratings, and enriched game data

Tables added:
- cafe_games: Game catalog enriched with BGG metadata
- users: Phone-number-based user identity
- sessions: Per-visit session tracking
- user_preferences: Extracted preferences with confidence scores
- ratings: Thumbs up/down/sideways per game
- conversations: Chat history for preference extraction
- recommendation_log: What was recommended and whether it was selected
"""

import sqlite3
import json
import os
from datetime import datetime

DB_PATH = "game_library.db"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def init_recommendation_tables():
    """
    Create recommendation tables. Safe to call repeatedly (IF NOT EXISTS).
    Does NOT touch existing rules-assistant tables (games, chunks, processed_files).
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Cafe game catalog — the authoritative list of games on the shelves.
    # Links to rules assistant via name match (cafe_games.name == games.title).
    # Links to BGG universe via bgg_id.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cafe_games (
            game_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            bgg_id INTEGER UNIQUE,

            -- BGG metadata (populated by onboard_game.py or sync script)
            complexity REAL,
            geek_rating REAL,
            avg_rating REAL,
            users_rated INTEGER,
            bgg_rank INTEGER,
            year_published INTEGER,
            min_players INTEGER,
            max_players INTEGER,
            playtime INTEGER,

            -- Categorization (JSON arrays)
            categories TEXT,        -- '["Strategy", "Economic"]'
            mechanics TEXT,         -- '["Engine Building", "Drafting"]'
            themes TEXT,            -- '["Medieval", "Fantasy"]'
            designers TEXT,         -- '["Jamey Stegmaier"]'
            publishers TEXT,        -- '["Stonemaier Games"]'

            -- Cafe-specific
            in_stock BOOLEAN DEFAULT 1,
            copies_available INTEGER DEFAULT 1,
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_bgg_sync TIMESTAMP,

            -- Computed from local ratings (updated by trigger or app code)
            cafe_rating REAL,
            cafe_play_count INTEGER DEFAULT 0,
            cafe_popularity_score REAL
        )
    """)

    # Users — phone number is the sole identifier
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,           -- UUID
            phone_number TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_visit TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            visit_count INTEGER DEFAULT 1
        )
    """)

    # Sessions — one per cafe visit
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,         -- UUID
            user_id TEXT NOT NULL,
            table_number TEXT,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ended_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    # User preferences — extracted from conversation by Claude
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            preference_type TEXT NOT NULL,       -- complexity, theme, mechanic, player_count, duration, dislike
            preference_value TEXT NOT NULL,       -- "2-3", "strategy", "engine-building", "4", "60-90", "dice combat"
            confidence REAL DEFAULT 1.0,          -- 0.0-1.0
            extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            session_id TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
    """)

    # Ratings — thumbs up (1) / okay (0) / down (-1)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            game_id INTEGER NOT NULL,
            rating INTEGER NOT NULL CHECK (rating IN (-1, 0, 1)),
            notes TEXT,
            rated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            session_id TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (game_id) REFERENCES cafe_games(game_id),
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
    """)

    # Conversation history — for preference extraction
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    # Recommendation log — what we recommended and what happened
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recommendation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            recommended_game_id INTEGER NOT NULL,
            recommendation_score REAL,
            score_breakdown TEXT,                -- JSON: {"content": 0.8, "collab": 0.0, ...}
            algorithm_version TEXT DEFAULT 'v1',
            position INTEGER,                    -- 1st, 2nd, 3rd
            was_selected BOOLEAN DEFAULT 0,
            recommended_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (recommended_game_id) REFERENCES cafe_games(game_id)
        )
    """)

    # Indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cafe_games_bgg_id ON cafe_games(bgg_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cafe_games_name ON cafe_games(name)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone_number)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_preferences_user ON user_preferences(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ratings_user ON ratings(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ratings_game ON ratings(game_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reclog_session ON recommendation_log(session_id)")

    conn.commit()
    conn.close()
    print("✅ Recommendation tables initialized")


# ---------------------------------------------------------------------------
# cafe_games CRUD
# ---------------------------------------------------------------------------

def upsert_cafe_game(name, bgg_id=None, **kwargs):
    """
    Insert or update a cafe game. Uses name as the unique key.
    Pass any cafe_games column as a kwarg.

    Returns game_id.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Check if exists
    cursor.execute("SELECT game_id FROM cafe_games WHERE name = ?", (name,))
    existing = cursor.fetchone()

    if existing:
        game_id = existing[0]
        # Build SET clause from kwargs + bgg_id
        updates = {}
        if bgg_id is not None:
            updates["bgg_id"] = bgg_id
        updates.update(kwargs)

        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [game_id]
            cursor.execute(f"UPDATE cafe_games SET {set_clause} WHERE game_id = ?", values)
        conn.commit()
    else:
        columns = ["name"]
        values = [name]
        if bgg_id is not None:
            columns.append("bgg_id")
            values.append(bgg_id)
        for k, v in kwargs.items():
            columns.append(k)
            values.append(v)

        placeholders = ", ".join("?" for _ in values)
        col_str = ", ".join(columns)
        cursor.execute(f"INSERT INTO cafe_games ({col_str}) VALUES ({placeholders})", values)
        game_id = cursor.lastrowid
        conn.commit()

    conn.close()
    return game_id


def get_cafe_game(name=None, bgg_id=None, game_id=None):
    """Retrieve a single cafe game by name, bgg_id, or game_id."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if game_id:
        cursor.execute("SELECT * FROM cafe_games WHERE game_id = ?", (game_id,))
    elif bgg_id:
        cursor.execute("SELECT * FROM cafe_games WHERE bgg_id = ?", (bgg_id,))
    elif name:
        cursor.execute("SELECT * FROM cafe_games WHERE name = ?", (name,))
    else:
        conn.close()
        return None

    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_cafe_games(in_stock_only=True):
    """Get all cafe games, optionally filtered to in-stock."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if in_stock_only:
        cursor.execute("SELECT * FROM cafe_games WHERE in_stock = 1 ORDER BY name")
    else:
        cursor.execute("SELECT * FROM cafe_games ORDER BY name")

    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_cafe_games_needing_sync():
    """Get cafe games that have a bgg_id but no metadata yet (or stale sync)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM cafe_games
        WHERE bgg_id IS NOT NULL
          AND (last_bgg_sync IS NULL OR complexity IS NULL)
        ORDER BY name
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def create_or_get_user(phone_number):
    """
    Find user by phone, or create new one. Returns user dict.
    """
    import uuid
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users WHERE phone_number = ?", (phone_number,))
    user = cursor.fetchone()

    if user:
        # Update last visit
        cursor.execute("""
            UPDATE users SET last_visit = CURRENT_TIMESTAMP, visit_count = visit_count + 1
            WHERE user_id = ?
        """, (user["user_id"],))
        conn.commit()
        user = dict(user)
    else:
        user_id = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO users (user_id, phone_number) VALUES (?, ?)",
            (user_id, phone_number)
        )
        conn.commit()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = dict(cursor.fetchone())

    conn.close()
    return user


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def create_session(user_id, table_number=None):
    """Create a new session for a visit. Returns session_id."""
    import uuid
    session_id = str(uuid.uuid4())
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO sessions (session_id, user_id, table_number) VALUES (?, ?, ?)",
        (session_id, user_id, table_number)
    )
    conn.commit()
    conn.close()
    return session_id


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

def save_preferences(user_id, session_id, preferences):
    """
    Save extracted preferences. `preferences` is a list of dicts:
    [{"type": "complexity", "value": "2-3", "confidence": 0.8}, ...]
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    for pref in preferences:
        cursor.execute("""
            INSERT INTO user_preferences (user_id, preference_type, preference_value, confidence, session_id)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, pref["type"], pref["value"], pref.get("confidence", 1.0), session_id))
    conn.commit()
    conn.close()


def get_user_preferences(user_id):
    """
    Get latest preferences for a user.
    Returns most recent preference per type (highest confidence wins ties).
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT preference_type, preference_value, confidence, extracted_at
        FROM user_preferences
        WHERE user_id = ?
        ORDER BY extracted_at DESC
    """, (user_id,))
    rows = cursor.fetchall()
    conn.close()

    # Deduplicate: keep most recent per (type, value), highest confidence wins
    seen = {}
    for r in rows:
        key = (r["preference_type"], r["preference_value"])
        if key not in seen or r["confidence"] > seen[key]["confidence"]:
            seen[key] = dict(r)

    return list(seen.values())


# ---------------------------------------------------------------------------
# Ratings
# ---------------------------------------------------------------------------

def save_rating(user_id, game_id, rating, session_id=None, notes=None):
    """Save a game rating. rating: 1 (loved), 0 (okay), -1 (not for us)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO ratings (user_id, game_id, rating, notes, session_id)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, game_id, rating, notes, session_id))
    conn.commit()
    conn.close()


def get_user_ratings(user_id):
    """Get all ratings for a user."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT r.*, cg.name as game_name
        FROM ratings r
        JOIN cafe_games cg ON r.game_id = cg.game_id
        WHERE r.user_id = ?
        ORDER BY r.rated_at DESC
    """, (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_game_ratings(game_id):
    """Get aggregate rating info for a game."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            COUNT(*) as total_ratings,
            SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END) as thumbs_up,
            SUM(CASE WHEN rating = 0 THEN 1 ELSE 0 END) as thumbs_mid,
            SUM(CASE WHEN rating = -1 THEN 1 ELSE 0 END) as thumbs_down,
            AVG(CAST(rating AS REAL)) as avg_rating
        FROM ratings
        WHERE game_id = ?
    """, (game_id,))
    row = cursor.fetchone()
    conn.close()
    return {
        "total_ratings": row[0],
        "thumbs_up": row[1],
        "thumbs_mid": row[2],
        "thumbs_down": row[3],
        "avg_rating": row[4]
    }


def update_cafe_game_ratings(game_id):
    """Recompute cafe_rating and cafe_play_count from ratings table."""
    stats = get_game_ratings(game_id)
    if stats["total_ratings"] == 0:
        return

    # cafe_rating: proportion of positive ratings (0-1 scale)
    cafe_rating = stats["thumbs_up"] / stats["total_ratings"]

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE cafe_games
        SET cafe_rating = ?, cafe_play_count = ?
        WHERE game_id = ?
    """, (cafe_rating, stats["total_ratings"], game_id))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

def save_message(session_id, user_id, role, content):
    """Save a conversation message."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO conversations (session_id, user_id, role, content)
        VALUES (?, ?, ?, ?)
    """, (session_id, user_id, role, content))
    conn.commit()
    conn.close()


def get_session_conversation(session_id, limit=50):
    """Get conversation history for a session."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT role, content, timestamp
        FROM conversations
        WHERE session_id = ?
        ORDER BY timestamp ASC
        LIMIT ?
    """, (session_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Recommendation log
# ---------------------------------------------------------------------------

def log_recommendation(session_id, user_id, game_id, score, breakdown, version, position):
    """Log a recommendation that was shown to a user."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO recommendation_log
            (session_id, user_id, recommended_game_id, recommendation_score,
             score_breakdown, algorithm_version, position)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (session_id, user_id, game_id, score,
          json.dumps(breakdown) if breakdown else None, version, position))
    conn.commit()
    conn.close()


def mark_recommendation_selected(session_id, game_id):
    """Mark that a user selected a recommended game."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE recommendation_log
        SET was_selected = 1
        WHERE session_id = ? AND recommended_game_id = ?
    """, (session_id, game_id))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def get_recommendation_stats():
    """Get high-level stats for the recommendation system."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    stats = {}

    cursor.execute("SELECT COUNT(*) FROM cafe_games WHERE in_stock = 1")
    stats["total_games"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM cafe_games WHERE bgg_id IS NOT NULL")
    stats["games_with_bgg"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users")
    stats["total_users"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM ratings")
    stats["total_ratings"] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM sessions")
    stats["total_sessions"] = cursor.fetchone()[0]

    conn.close()
    return stats
