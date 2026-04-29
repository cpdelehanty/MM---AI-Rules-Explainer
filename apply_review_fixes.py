"""
One-shot script applying the user's review-queue decisions to the cafe
library CSV, the BGG cache, and the overrides file. Idempotent — safe to
re-run, but designed to run once after the initial bulk enrichment.

After this runs, follow up with:
    py bulk_enrich_bgg.py            # picks up renames, replacements, overrides
    py bulk_enrich_bgg.py --patch-stats  # backfill community_player_age
    py bulk_enrich_bgg.py --csv-out       # write final CSV
"""
import csv
import json
import os
import shutil

CSV_PATH = "merry_meeple_game_library_v3.xlsx - Game Library.csv"
CACHE_PATH = "bgg_cache.json"
OVERRIDES_PATH = "bgg_overrides.json"

# Pure renames — same game, just match-canonical title
RENAMES = {
    "Orleans": "Orléans",
    "Pandemic: Season 0": "Pandemic Legacy: Season 0",
    "The Crew: Quest for Planet Nine": "The Crew: The Quest for Planet Nine",
    "Mysterium Kids": "Mysterium Kids: Captain Echo's Treasure",
    "Railroad Ink": "Railroad Ink: Deep Blue Edition",
    "Blockbuster: The Game": "Blockbuster Movie Game",
    "Unmatched: Battle of Legends": "Unmatched: Battle of Legends, Volume One",
    "The New York Chase": "N.Y. Chase",
    "Secret Roles: Samurai": "Samurai",
}

# Replacements — different game in CSV, drop old cache entry
REPLACEMENTS = {
    "Catan: Backstabber Edition": "Concordia",
    "Deductive Detective": "Istanbul",
    "Wingspan: Complete Aviary Edition": "Cosmic Encounter",
}

# BGG ID overrides for known mismatches. Keyed by the *post-rename* CSV name.
OVERRIDES = {
    "Arkham Horror: 3rd Edition":       257499,
    "Flashpoint: Fire Rescue":          100901,
    "Mansions of Madness: 2nd Ed.":     205059,
    "Runebound 3rd Edition":            181530,
    "Twilight Imperium 4th Edition":    233078,
    "War of the Ring: 2nd Edition":     115746,
    "My First Orchard (HABA)":           41302,
    "Spot It! Kids":                    117995,
    "Perquacky":                          1161,
    "Clank!":                           201808,
    "Lewis & Clark":                    140620,
    "Sword & Sorcery":                  170771,
    "Ticket to Ride: Rails and Sails":  202670,
    "Five Tribes":                      157354,
    "Dead of Winter":                   150376,
    "Quacks of Quedlinburg":            244521,
    "Unicorn Glitterluck":              159566,
    "Thunderstone Advance":             116998,
    "Disney Villainous":                256382,
}


def main():
    # ----- 1. CSV edits -----
    backup = CSV_PATH + ".bak"
    if not os.path.exists(backup):
        shutil.copy(CSV_PATH, backup)
        print(f"Backed up CSV to {backup}")

    with open(CSV_PATH, encoding="utf-8") as f:
        rows = list(csv.reader(f))

    name_col = None
    for h in rows[2]:
        if h.strip() == "Game Name":
            name_col = rows[2].index(h)
            break
    assert name_col is not None, "Couldn't find Game Name column"

    csv_changes = 0
    for r in rows[3:]:
        if len(r) <= name_col:
            continue
        original = r[name_col].strip()
        if original in RENAMES:
            r[name_col] = RENAMES[original]
            print(f"  CSV rename: {original!r} -> {RENAMES[original]!r}")
            csv_changes += 1
        elif original in REPLACEMENTS:
            r[name_col] = REPLACEMENTS[original]
            print(f"  CSV replace: {original!r} -> {REPLACEMENTS[original]!r}")
            csv_changes += 1

    with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(rows)
    print(f"CSV: {csv_changes} rows updated, written to {CSV_PATH}")

    # ----- 2. Cache surgery -----
    with open(CACHE_PATH, encoding="utf-8") as f:
        cache = json.load(f)

    cache_changes = 0
    # Rename cache keys
    for old, new in RENAMES.items():
        if old in cache:
            entry = cache.pop(old)
            entry["name"] = new
            cache[new] = entry
            print(f"  cache rename: {old!r} -> {new!r}")
            cache_changes += 1

    # Drop replaced entries (will be re-enriched on next run)
    for old in REPLACEMENTS:
        if old in cache:
            del cache[old]
            print(f"  cache drop: {old!r}")
            cache_changes += 1

    # Drop entries that need to be re-fetched with an override
    for name in OVERRIDES:
        if name in cache:
            del cache[name]
            print(f"  cache drop (will re-enrich): {name!r}")
            cache_changes += 1

    # Drop the renamed unfindable entries' caches too — they'll re-enrich
    # under their new names with auto-match.
    for new_name in ("N.Y. Chase", "Samurai"):
        if new_name in cache:
            # Old cache may have been dragged in via rename; drop so we re-search
            del cache[new_name]
            print(f"  cache drop (re-search needed): {new_name!r}")
            cache_changes += 1

    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)
    print(f"Cache: {cache_changes} entries modified, {len(cache)} remaining")

    # ----- 3. Overrides -----
    if os.path.exists(OVERRIDES_PATH):
        with open(OVERRIDES_PATH, encoding="utf-8") as f:
            overrides = json.load(f)
    else:
        overrides = {}
    overrides.update(OVERRIDES)
    with open(OVERRIDES_PATH, "w", encoding="utf-8") as f:
        json.dump(overrides, f, indent=2, ensure_ascii=False)
    print(f"Overrides: {len(OVERRIDES)} added, written to {OVERRIDES_PATH}")

    print()
    print("Next steps:")
    print("  py bulk_enrich_bgg.py            # fill new/dropped entries")
    print("  py bulk_enrich_bgg.py --patch-stats  # backfill community_player_age")
    print("  py bulk_enrich_bgg.py --csv-out       # write final CSV")


if __name__ == "__main__":
    main()
