"""
Load enriched BGG metadata (bgg_cache.json) and the cafe library CSV
into the cafe_games table. Idempotent — safe to re-run after enrichment.

Usage:
    python sync_bgg_to_cafe_games.py            # full sync
    python sync_bgg_to_cafe_games.py --dry-run  # preview only

Adds extra columns the original `database_recs.init_recommendation_tables`
schema doesn't cover (community_age, fans_also_like, image, etc.).
"""

import argparse
import csv
import json
import os
import sqlite3
from datetime import datetime

from database_recs import init_recommendation_tables, DB_PATH

CSV_PATH = "merry_meeple_game_library_v3.xlsx - Game Library.csv"
CACHE_PATH = "bgg_cache.json"

# Columns we add on top of the database_recs schema. Each is added with
# ALTER TABLE if missing — survives idempotent re-runs.
EXTRA_COLUMNS = [
    ("description", "TEXT"),
    ("short_description", "TEXT"),
    ("image_url", "TEXT"),
    ("bgg_url", "TEXT"),
    ("min_age_manufacturer", "INTEGER"),
    ("community_player_age", "TEXT"),
    ("min_playtime", "INTEGER"),
    ("max_playtime", "INTEGER"),
    ("best_player_count", "TEXT"),         # JSON list of {min,max} ranges
    ("recommended_player_count", "TEXT"),  # JSON list of {min,max} ranges
    ("fans_also_like", "TEXT"),            # JSON list of {bgg_id,name,rating}
    ("subdomain_ranks", "TEXT"),           # JSON {strategygames: 579, ...}
    ("language_dependence", "TEXT"),
    ("cafe_categories", "TEXT"),           # JSON list — game can belong to multiple
    ("nyc_themed", "INTEGER"),             # 1 if CSV "NYC" column was set
    ("match_confidence", "REAL"),
    ("match_method", "TEXT"),
]


def parse_csv(path, cache):
    """
    Parse the CSV, merging duplicate listings (same game in multiple categories).
    Dedup key: bgg_id when the cache has it, else normalized name. Returns
    list of {name, categories: [...], nyc, _bgg_id}.
    """
    with open(path, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    keys = [h.strip() for h in rows[2]]

    raw = []
    for r in rows[3:]:
        if not any(r):
            continue
        if len(r) < len(keys):
            r = r + [""] * (len(keys) - len(r))
        rec = dict(zip(keys, r))
        if not rec.get("Game Name"):
            continue
        name = rec["Game Name"].strip()
        bgg_id = (cache.get(name) or {}).get("bgg_id")
        raw.append({
            "name": name,
            "category": rec.get("Category", "").strip(),
            "nyc": bool(rec.get("NYC", "").strip()),
            "bgg_id": bgg_id,
        })

    # Merge rows that share a bgg_id (or share a name when no bgg_id).
    merged = {}
    order = []
    for r in raw:
        key = ("bgg", r["bgg_id"]) if r["bgg_id"] else ("name", r["name"])
        if key in merged:
            existing = merged[key]
            if r["category"] and r["category"] not in existing["categories"]:
                existing["categories"].append(r["category"])
            existing["nyc"] = existing["nyc"] or r["nyc"]
        else:
            merged[key] = {
                "name": r["name"],
                "categories": [r["category"]] if r["category"] else [],
                "nyc": r["nyc"],
                "bgg_id": r["bgg_id"],
            }
            order.append(key)

    return [merged[k] for k in order]


def add_extra_columns_if_missing(conn):
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(cafe_games)")
    existing = {row[1] for row in cur.fetchall()}
    added = []
    for col, type_ in EXTRA_COLUMNS:
        if col not in existing:
            cur.execute(f"ALTER TABLE cafe_games ADD COLUMN {col} {type_}")
            added.append(col)
    conn.commit()
    return added


def upsert_full(conn, game, dry_run=False):
    """
    Upsert a cafe game. Match by bgg_id first (so renames carry through),
    then by name. Both name and bgg_id have UNIQUE constraints.
    """
    cur = conn.cursor()
    existing = None
    if game.get("bgg_id"):
        cur.execute("SELECT game_id FROM cafe_games WHERE bgg_id = ?", (game["bgg_id"],))
        existing = cur.fetchone()
    if not existing:
        cur.execute("SELECT game_id FROM cafe_games WHERE name = ?", (game["name"],))
        existing = cur.fetchone()

    columns = list(game.keys())
    values = [game[c] for c in columns]

    if existing:
        if dry_run:
            return existing[0], "would-update"
        # Update everything including name (handles renames after BGG match)
        set_clause = ", ".join(f"{c} = ?" for c in columns)
        update_values = list(values) + [existing[0]]
        cur.execute(
            f"UPDATE cafe_games SET {set_clause} WHERE game_id = ?",
            update_values,
        )
        conn.commit()
        return existing[0], "updated"
    else:
        if dry_run:
            return None, "would-insert"
        placeholders = ", ".join("?" for _ in columns)
        col_str = ", ".join(columns)
        cur.execute(
            f"INSERT INTO cafe_games ({col_str}) VALUES ({placeholders})",
            values,
        )
        conn.commit()
        return cur.lastrowid, "inserted"


def build_row(csv_entry, cache_entry):
    """Merge CSV (in-stock + cafe_categories) with BGG cache (everything else)."""
    e = cache_entry or {}

    def js(v):
        return json.dumps(v) if v is not None else None

    bgg_id = e.get("bgg_id")
    bgg_url = f"https://boardgamegeek.com/boardgame/{bgg_id}" if bgg_id else None

    return {
        "name": csv_entry["name"],
        "bgg_id": bgg_id,

        # Core ratings (database_recs schema)
        "complexity": e.get("complexity"),
        "geek_rating": e.get("geek_rating"),
        "avg_rating": e.get("avg_rating"),
        "users_rated": e.get("users_rated"),
        "bgg_rank": e.get("bgg_rank"),

        # Identity
        "year_published": e.get("year_published"),
        "min_players": e.get("min_players"),
        "max_players": e.get("max_players"),
        "playtime": e.get("playtime"),

        # Lists
        "categories": js(e.get("categories")),
        "mechanics": js(e.get("mechanics")),
        "themes": js(e.get("themes")),
        "designers": js(e.get("designers")),
        "publishers": js(e.get("publishers")),

        # Cafe metadata
        "in_stock": 1,
        "last_bgg_sync": datetime.utcnow().isoformat(),

        # Extra columns
        "description": e.get("description"),
        "short_description": e.get("short_description"),
        "image_url": e.get("image_url"),
        "bgg_url": bgg_url,
        "min_age_manufacturer": e.get("min_age"),
        "community_player_age": e.get("community_player_age"),
        "min_playtime": e.get("min_playtime"),
        "max_playtime": e.get("max_playtime"),
        "best_player_count": js(e.get("best_player_count")),
        "recommended_player_count": js(e.get("recommended_player_count")),
        "fans_also_like": js(e.get("fans_also_like")),
        "subdomain_ranks": js(e.get("subdomain_ranks")),
        "language_dependence": e.get("language_dependence"),
        "cafe_categories": js(csv_entry["categories"]),
        "nyc_themed": 1 if csv_entry["nyc"] else 0,
        "match_confidence": e.get("match_confidence"),
        "match_method": e.get("match_method"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--csv", default=CSV_PATH)
    parser.add_argument("--cache", default=CACHE_PATH)
    args = parser.parse_args()

    init_recommendation_tables()

    with open(args.cache, encoding="utf-8") as f:
        cache = json.load(f)

    csv_games = parse_csv(args.csv, cache)
    multi_cat = sum(1 for g in csv_games if len(g["categories"]) > 1)
    print(f"CSV: {len(csv_games)} games (after merging {multi_cat} multi-category listings), "
          f"cache: {len(cache)} entries")

    conn = sqlite3.connect(DB_PATH)
    added = add_extra_columns_if_missing(conn)
    if added:
        print(f"Added columns: {', '.join(added)}")

    # Migration: clear obsolete single-category column from prior runs
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(cafe_games)")
    cols = {row[1] for row in cur.fetchall()}
    if "cafe_category" in cols:
        # Don't drop the column (SQLite makes that hard) — just leave it stale.
        pass

    inserted = updated = no_bgg = 0
    for csv_entry in csv_games:
        cache_entry = cache.get(csv_entry["name"])
        row = build_row(csv_entry, cache_entry)
        if not cache_entry or not cache_entry.get("bgg_id"):
            no_bgg += 1
        _, status = upsert_full(conn, row, dry_run=args.dry_run)
        if status in ("inserted", "would-insert"):
            inserted += 1
        elif status in ("updated", "would-update"):
            updated += 1

    conn.close()
    verb = "would " if args.dry_run else ""
    print(f"{verb}inserted: {inserted}")
    print(f"{verb}updated:  {updated}")
    print(f"without BGG data: {no_bgg}  (puzzles/RPG modules — kept as-is)")


if __name__ == "__main__":
    main()
