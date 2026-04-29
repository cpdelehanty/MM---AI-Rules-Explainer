"""
Onboard a game to the Merry Meeple cafe library.

Workflow:
  1. Search BGG by name → show candidates with year/ID
  2. You pick the correct match
  3. Fetch full metadata from BGG XML API (stats, categories, mechanics, etc.)
  4. Store in cafe_games table with bgg_id

Usage:
  python onboard_game.py "Wingspan"
  python onboard_game.py "Catan"
  python onboard_game.py --bulk              # onboard all games in rules assistant DB
  python onboard_game.py --list              # show current cafe_games
  python onboard_game.py --sync              # re-fetch BGG data for all mapped games

Requires: requests (pip install requests)
"""

import argparse
import sys
import time
import json
import xml.etree.ElementTree as ET

try:
    import requests
except ImportError:
    print("❌ Missing dependency: pip install requests")
    sys.exit(1)

from database_recs import (
    init_recommendation_tables,
    upsert_cafe_game,
    get_cafe_game,
    get_all_cafe_games,
    get_cafe_games_needing_sync,
)

# Also import from existing rules DB to find games without BGG mapping
try:
    from database import init_database, get_all_games
except ImportError:
    get_all_games = None

BGG_API_BASE = "https://boardgamegeek.com/xmlapi2"
BGG_RATE_LIMIT = 5  # seconds between requests per BGG guidelines


# ---------------------------------------------------------------------------
# BGG API helpers
# ---------------------------------------------------------------------------

def search_bgg(query):
    """
    Search BGG for board games matching a query string.
    Returns list of (bgg_id, name, year) tuples.
    """
    url = f"{BGG_API_BASE}/search"
    params = {"query": query, "type": "boardgame"}

    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    results = []
    for item in root.findall("item"):
        bgg_id = int(item.get("id"))
        name_el = item.find("name")
        name = name_el.get("value") if name_el is not None else "Unknown"
        year_el = item.find("yearpublished")
        year = year_el.get("value") if year_el is not None else "?"
        results.append((bgg_id, name, year))

    return results


def fetch_bgg_game(bgg_id):
    """
    Fetch full game metadata from BGG by ID.
    Returns dict with all fields needed for cafe_games.
    """
    url = f"{BGG_API_BASE}/thing"
    params = {"id": bgg_id, "stats": 1, "type": "boardgame"}

    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    item = root.find("item")
    if item is None:
        return None

    def _val(tag, attr="value"):
        el = item.find(tag)
        return el.get(attr) if el is not None else None

    def _float(tag, attr="value"):
        v = _val(tag, attr)
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _int(tag, attr="value"):
        v = _val(tag, attr)
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    # Collect all links by type
    def _links(link_type):
        return [
            link.get("value")
            for link in item.findall("link")
            if link.get("type") == link_type
        ]

    # Primary name
    name = None
    for name_el in item.findall("name"):
        if name_el.get("type") == "primary":
            name = name_el.get("value")
            break

    # Stats are nested: statistics > ratings > ...
    stats = item.find("statistics")
    ratings = stats.find("ratings") if stats is not None else None

    def _rating_val(tag):
        if ratings is None:
            return None
        el = ratings.find(tag)
        if el is None:
            return None
        v = el.get("value")
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _rating_int(tag):
        v = _rating_val(tag)
        return int(v) if v is not None else None

    # BGG rank — nested inside ratings > ranks > rank
    bgg_rank = None
    if ratings is not None:
        ranks_el = ratings.find("ranks")
        if ranks_el is not None:
            for rank in ranks_el.findall("rank"):
                if rank.get("name") == "boardgame":
                    try:
                        bgg_rank = int(rank.get("value"))
                    except (TypeError, ValueError):
                        bgg_rank = None

    categories = _links("boardgamecategory")
    mechanics = _links("boardgamemechanic")
    designers = _links("boardgamedesigner")
    publishers = _links("boardgamepublisher")
    families = _links("boardgamefamily")

    desc_el = item.find("description")
    description = desc_el.text.strip() if desc_el is not None and desc_el.text else None

    # BGG doesn't have a separate "themes" field — themes are a subset of families.
    # We'll store families and treat categories as the primary thematic signal.
    themes = [
        f for f in families
        if not f.startswith("Admin:")
        and not f.startswith("Components:")
        and not f.startswith("Mechanism:")
        and not f.startswith("Players:")
        and not f.startswith("Game:")
        and not f.startswith("Misc:")
    ]

    return {
        "name": name,
        "bgg_id": bgg_id,
        "year_published": _int("yearpublished"),
        "min_players": _int("minplayers"),
        "max_players": _int("maxplayers"),
        "playtime": _int("playingtime"),
        "complexity": _rating_val("averageweight"),
        "avg_rating": _rating_val("average"),
        "geek_rating": _rating_val("bayesaverage"),
        "users_rated": _rating_int("usersrated"),
        "bgg_rank": bgg_rank,
        "description": description,
        "categories": json.dumps(categories),
        "mechanics": json.dumps(mechanics),
        "themes": json.dumps(themes),
        "designers": json.dumps(designers),
        "publishers": json.dumps(publishers),
    }


# ---------------------------------------------------------------------------
# CLI actions
# ---------------------------------------------------------------------------

def onboard_interactive(query):
    """Search BGG, let user confirm, fetch metadata, store."""

    # Check if already mapped
    existing = get_cafe_game(name=query)
    if existing and existing.get("bgg_id"):
        print(f"\n⚡ \"{query}\" already mapped to BGG ID {existing['bgg_id']}")
        print(f"   Complexity: {existing.get('complexity')}, "
              f"Rating: {existing.get('avg_rating')}, "
              f"Rank: #{existing.get('bgg_rank')}")
        resp = input("   Re-fetch BGG data? (y/n): ").strip().lower()
        if resp != "y":
            return
        bgg_id = existing["bgg_id"]
    else:
        # Search BGG
        print(f"\n🔍 Searching BGG for \"{query}\"...")
        results = search_bgg(query)

        if not results:
            print("   No results found on BGG.")
            return

        # Show candidates
        print(f"\n   Found {len(results)} match(es):\n")
        for i, (bgg_id, name, year) in enumerate(results[:15], 1):
            print(f"   {i:>2}. {name} ({year}) — BGG ID: {bgg_id}")

        print(f"\n   Enter number to confirm, or 's' to skip: ", end="")
        choice = input().strip()

        if choice.lower() == "s" or not choice:
            print("   Skipped.")
            return

        try:
            idx = int(choice) - 1
            bgg_id, matched_name, matched_year = results[idx]
        except (ValueError, IndexError):
            print("   Invalid choice.")
            return

        print(f"\n   ✅ Selected: {matched_name} ({matched_year}) — BGG ID: {bgg_id}")

    # Fetch full metadata
    print(f"   📡 Fetching metadata from BGG...")
    time.sleep(BGG_RATE_LIMIT)  # respect rate limit
    game_data = fetch_bgg_game(bgg_id)

    if not game_data:
        print("   ❌ Failed to fetch game data.")
        return

    # Use the query name (our local name) not BGG's canonical name
    # This preserves the match to our rules assistant's game title
    local_name = query

    # Store in cafe_games
    game_id = upsert_cafe_game(
        name=local_name,
        bgg_id=game_data["bgg_id"],
        complexity=game_data["complexity"],
        geek_rating=game_data["geek_rating"],
        avg_rating=game_data["avg_rating"],
        users_rated=game_data["users_rated"],
        bgg_rank=game_data["bgg_rank"],
        year_published=game_data["year_published"],
        min_players=game_data["min_players"],
        max_players=game_data["max_players"],
        playtime=game_data["playtime"],
        categories=game_data["categories"],
        mechanics=game_data["mechanics"],
        themes=game_data["themes"],
        designers=game_data["designers"],
        publishers=game_data["publishers"],
        last_bgg_sync="now",
    )

    # Print summary
    cats = json.loads(game_data["categories"]) if game_data["categories"] else []
    mechs = json.loads(game_data["mechanics"]) if game_data["mechanics"] else []
    print(f"\n   📋 {local_name} (BGG #{game_data['bgg_rank']})")
    print(f"      Complexity: {game_data['complexity']:.1f}/5")
    print(f"      Rating: {game_data['avg_rating']:.1f}/10 "
          f"({game_data['users_rated']:,} ratings)")
    print(f"      Players: {game_data['min_players']}-{game_data['max_players']}, "
          f"{game_data['playtime']} min")
    print(f"      Categories: {', '.join(cats[:5])}")
    print(f"      Mechanics: {', '.join(mechs[:5])}")
    print(f"\n   ✅ Saved to cafe_games (game_id={game_id})")


def onboard_bulk():
    """
    Find all games in the rules assistant DB that aren't in cafe_games yet,
    and prompt to onboard each one.
    """
    if get_all_games is None:
        print("❌ Can't import from database.py — are you in the right directory?")
        return

    init_database()
    rules_games = get_all_games()
    cafe_games = {g["name"] for g in get_all_cafe_games(in_stock_only=False)}

    unmapped = [g for g in rules_games if g["title"] not in cafe_games]

    if not unmapped:
        print("\n✅ All rules-assistant games already in cafe_games!")
        list_cafe_games()
        return

    print(f"\n📋 {len(unmapped)} game(s) need BGG mapping:\n")
    for g in unmapped:
        print(f"   • {g['title']}")

    print()
    for g in unmapped:
        print(f"\n{'='*60}")
        onboard_interactive(g["title"])
        print()


def sync_all():
    """Re-fetch BGG metadata for all mapped games."""
    games = get_all_cafe_games(in_stock_only=False)
    mapped = [g for g in games if g.get("bgg_id")]

    if not mapped:
        print("No games with BGG IDs to sync.")
        return

    print(f"\n🔄 Syncing {len(mapped)} game(s) from BGG...\n")
    for g in mapped:
        print(f"   📡 {g['name']} (BGG ID: {g['bgg_id']})...", end=" ")
        time.sleep(BGG_RATE_LIMIT)

        try:
            data = fetch_bgg_game(g["bgg_id"])
            if data:
                upsert_cafe_game(
                    name=g["name"],
                    bgg_id=data["bgg_id"],
                    complexity=data["complexity"],
                    geek_rating=data["geek_rating"],
                    avg_rating=data["avg_rating"],
                    users_rated=data["users_rated"],
                    bgg_rank=data["bgg_rank"],
                    year_published=data["year_published"],
                    min_players=data["min_players"],
                    max_players=data["max_players"],
                    playtime=data["playtime"],
                    categories=data["categories"],
                    mechanics=data["mechanics"],
                    themes=data["themes"],
                    designers=data["designers"],
                    publishers=data["publishers"],
                    last_bgg_sync="now",
                )
                print(f"✅ (rating: {data['avg_rating']:.1f}, "
                      f"complexity: {data['complexity']:.1f})")
            else:
                print("❌ no data returned")
        except Exception as e:
            print(f"❌ {e}")

    print(f"\n✅ Sync complete.")


def list_cafe_games():
    """Show all games in cafe_games."""
    games = get_all_cafe_games(in_stock_only=False)

    if not games:
        print("\n📋 No games in cafe_games yet.")
        print("   Run: python onboard_game.py --bulk")
        return

    print(f"\n🎲 Cafe Game Library ({len(games)} games):\n")
    print(f"   {'Name':<25} {'BGG ID':>7} {'Cplx':>5} {'Rating':>7} "
          f"{'Rank':>6} {'Players':>8} {'Time':>5}")
    print(f"   {'─'*25} {'─'*7} {'─'*5} {'─'*7} {'─'*6} {'─'*8} {'─'*5}")

    for g in games:
        bgg = g.get("bgg_id") or "—"
        cplx = f"{g['complexity']:.1f}" if g.get("complexity") else "—"
        rating = f"{g['avg_rating']:.1f}/10" if g.get("avg_rating") else "—"
        rank = f"#{g['bgg_rank']}" if g.get("bgg_rank") else "—"
        players = (f"{g['min_players']}-{g['max_players']}"
                   if g.get("min_players") else "—")
        playtime = f"{g['playtime']}m" if g.get("playtime") else "—"

        print(f"   {g['name']:<25} {str(bgg):>7} {cplx:>5} {rating:>7} "
              f"{rank:>6} {players:>8} {playtime:>5}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Onboard games to Merry Meeple cafe library with BGG metadata"
    )
    parser.add_argument("game_name", nargs="?", help="Game name to search on BGG")
    parser.add_argument("--bulk", action="store_true",
                        help="Onboard all games from rules assistant DB")
    parser.add_argument("--list", action="store_true",
                        help="List all games in cafe_games")
    parser.add_argument("--sync", action="store_true",
                        help="Re-fetch BGG data for all mapped games")

    args = parser.parse_args()

    # Always ensure tables exist
    init_recommendation_tables()

    if args.list:
        list_cafe_games()
    elif args.sync:
        sync_all()
    elif args.bulk:
        onboard_bulk()
    elif args.game_name:
        onboard_interactive(args.game_name)
    else:
        parser.print_help()
        print("\nExamples:")
        print('  python onboard_game.py "Wingspan"')
        print('  python onboard_game.py --bulk')
        print('  python onboard_game.py --list')
        print('  python onboard_game.py --sync')


if __name__ == "__main__":
    main()
