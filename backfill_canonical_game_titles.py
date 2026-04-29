"""
One-time fix: rename existing rules-assistant games (in `games` table)
to their canonical cafe_games.name when they differ.

Safe to re-run — idempotent.

Why: process_rulebooks.py used to derive titles purely from filenames
(e.g. '7 Wonders Duel'). The cafe library uses canonical BGG-aligned
names (e.g. '7 Wonders: Duel'). After this backfill, games.title ==
cafe_games.name for every cafe-library game, so the rules-assistant and
the recommendation engine share a single key.
"""
import sqlite3
from process_rulebooks import resolve_to_cafe_name
from database import DB_PATH


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, title FROM games ORDER BY title")
    games = cur.fetchall()

    renamed = unchanged = collisions = 0
    for game_id, old in games:
        new = resolve_to_cafe_name(old)
        if new == old:
            unchanged += 1
            continue
        # Check for collision: a games row with the new title may already exist
        cur.execute("SELECT id FROM games WHERE title = ? AND id != ?", (new, game_id))
        existing = cur.fetchone()
        if existing:
            print(f"  COLLISION: cannot rename {old!r} -> {new!r} "
                  f"(another row already has that title, id={existing[0]})")
            collisions += 1
            continue
        cur.execute("UPDATE games SET title = ? WHERE id = ?", (new, game_id))
        print(f"  renamed: {old!r} -> {new!r}")
        renamed += 1

    conn.commit()
    conn.close()
    print()
    print(f"renamed:   {renamed}")
    print(f"unchanged: {unchanged}")
    print(f"collisions: {collisions}")


if __name__ == "__main__":
    main()
