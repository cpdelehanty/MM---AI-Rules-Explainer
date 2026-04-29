"""
Bulk-enrich the Merry Meeple game library with BGG metadata.

Reads the game library CSV, looks up each title on BGG, and saves rich
metadata (rank, rating, weight, description, designers, categories,
mechanics, "fans also like") to a JSON cache. Resumable across runs.

Uses BGG's public JSON endpoints (browser user-agent required), since
the xmlapi2 was put behind authentication.

  Search:      https://boardgamegeek.com/search/boardgame?q=NAME
  Overview:    https://api.geekdo.com/api/geekitems?objectid=ID&objecttype=thing
                 &showcount=10&pageid=1&linkdata_index=overview
  Stats:       https://api.geekdo.com/api/dynamicinfo?objectid=ID&objecttype=thing
  Fans like:   https://api.geekdo.com/api/geekitem/recs?objectid=ID&objecttype=thing

Usage:
  python bulk_enrich_bgg.py                         # enrich all unprocessed games
  python bulk_enrich_bgg.py --review                # show low-confidence matches
  python bulk_enrich_bgg.py --csv-out               # write enriched CSV from cache
  python bulk_enrich_bgg.py --only "Catan"          # enrich a single title (testing)
  python bulk_enrich_bgg.py --refresh "Catan"       # re-fetch even if cached
  python bulk_enrich_bgg.py --override "Catan" 13   # force a BGG ID match

Files written:
  bgg_cache.json                               cache (source of truth)
  bgg_overrides.json                           manual name -> bgg_id map
  bgg_match_review.json                        low-confidence matches list
  merry_meeple_game_library_enriched.csv       enriched CSV
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    print("Missing dependency: pip install requests")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CSV_INPUT = "merry_meeple_game_library_v3.xlsx - Game Library.csv"
CSV_OUTPUT = "merry_meeple_game_library_enriched.csv"
CACHE_FILE = "bgg_cache.json"
OVERRIDES_FILE = "bgg_overrides.json"
REVIEW_FILE = "bgg_match_review.json"

REQUEST_DELAY = 2.0      # seconds between BGG API calls
BACKOFF_BASE = 8.0       # exponential backoff base on errors
MAX_RETRIES = 5
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}

SKIP_CATEGORIES = {"Puzzles"}  # cafe-library categories to skip

# ---------------------------------------------------------------------------
# CSV / cache helpers
# ---------------------------------------------------------------------------

def normalize_name(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def parse_csv(path):
    """
    Parse the cafe library CSV. Header is at row index 2 (3rd line).
    Returns list of dicts: {row, name, category, nyc, csv_rank, csv_rating, csv_weight}.
    """
    with open(path, encoding="utf-8") as f:
        rows = list(csv.reader(f))

    keys = [h.strip() for h in rows[2]]
    out = []
    for i, row in enumerate(rows[3:], start=4):
        if not any(row):
            continue
        if len(row) < len(keys):
            row = row + [""] * (len(keys) - len(row))
        rec = dict(zip(keys, row))
        if not rec.get("Game Name"):
            continue
        out.append({
            "row": i,
            "name": rec["Game Name"].strip(),
            "category": rec.get("Category", "").strip(),
            "nyc": rec.get("NYC", "").strip(),
            "csv_rank": rec.get("BGG Rank", "").strip(),
            "csv_rating": rec.get("Rating", "").strip(),
            "csv_weight": rec.get("Weight", "").strip(),
        })
    return out


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# BGG HTTP with retry/backoff
# ---------------------------------------------------------------------------

def _retry_get_json(url, params=None):
    """GET expecting JSON, with retries on 429/5xx and parse errors."""
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, headers=HTTP_HEADERS, timeout=20)
        except requests.RequestException as e:
            wait = BACKOFF_BASE * (2 ** attempt)
            print(f"    [retry] network error: {e} — sleeping {wait}s")
            time.sleep(wait)
            last_err = e
            continue

        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as e:
                last_err = e
                wait = BACKOFF_BASE * (2 ** attempt)
                print(f"    [retry] non-JSON response — sleeping {wait}s")
                time.sleep(wait)
                continue

        if resp.status_code in (429, 500, 502, 503, 504):
            wait = BACKOFF_BASE * (2 ** attempt)
            print(f"    [retry] HTTP {resp.status_code} — sleeping {wait}s")
            time.sleep(wait)
            continue

        resp.raise_for_status()
    raise RuntimeError(f"Max retries exhausted for {url}: {last_err}")


# ---------------------------------------------------------------------------
# BGG endpoints (JSON, public)
# ---------------------------------------------------------------------------

def _curl_get_json(url):
    """
    Shell out to curl to bypass Cloudflare TLS fingerprinting on
    boardgamegeek.com. Returns parsed JSON or raises.
    """
    last_err = None
    for attempt in range(MAX_RETRIES):
        proc = subprocess.run(
            ["curl", "-s", "--fail",
             "-A", HTTP_HEADERS["User-Agent"],
             "-H", "Accept: application/json",
             "-H", "X-Requested-With: XMLHttpRequest",
             url],
            capture_output=True, timeout=20,
        )
        if proc.returncode == 0 and proc.stdout:
            try:
                return json.loads(proc.stdout)
            except json.JSONDecodeError as e:
                last_err = e
        else:
            last_err = proc.stderr.decode("utf-8", errors="replace")[:200]
        wait = BACKOFF_BASE * (2 ** attempt)
        print(f"    [retry] curl failed ({last_err!r}) — sleeping {wait}s")
        time.sleep(wait)
    raise RuntimeError(f"curl max retries exhausted for {url}: {last_err}")


def bgg_search(query):
    """Search by name. Returns [{bgg_id, name, year}, ...]."""
    # Cloudflare blocks python-requests on boardgamegeek.com (TLS fingerprint),
    # but lets curl through. So we shell out for this one endpoint.
    from urllib.parse import urlencode
    qs = urlencode({"q": query, "showcount": 20, "nosession": 1})
    data = _curl_get_json(f"https://boardgamegeek.com/search/boardgame?{qs}")
    items = data.get("items", []) if isinstance(data, dict) else []
    out = []
    for it in items:
        try:
            bgg_id = int(it.get("objectid"))
        except (TypeError, ValueError):
            continue
        out.append({
            "bgg_id": bgg_id,
            "name": it.get("name") or "",
            "year": it.get("yearpublished"),
        })
    return out


def bgg_overview(bgg_id):
    """
    /api/geekitems with linkdata_index=overview. Returns description, year,
    players, playtime, designers, categories, mechanics, publishers, families.
    """
    url = "https://api.geekdo.com/api/geekitems"
    data = _retry_get_json(url, params={
        "objectid": bgg_id,
        "objecttype": "thing",
        "showcount": 10,
        "pageid": 1,
        "linkdata_index": "overview",
    })
    item = data.get("item", {}) if isinstance(data, dict) else {}
    if not item:
        return None

    links = item.get("links", {}) or {}

    def link_names(key):
        vals = links.get(key) or []
        if not isinstance(vals, list):
            return []
        return [v.get("name") for v in vals if v.get("name")]

    def to_int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    families = link_names("boardgamefamily")
    themes = [
        f for f in families
        if not f.startswith(("Admin:", "Components:", "Mechanism:",
                              "Players:", "Game:", "Misc:",
                              "Digital Implementations:"))
    ]

    return {
        "bgg_name": item.get("name"),
        "year_published": to_int(item.get("yearpublished")),
        "min_players": to_int(item.get("minplayers")),
        "max_players": to_int(item.get("maxplayers")),
        "min_playtime": to_int(item.get("minplaytime")),
        "max_playtime": to_int(item.get("maxplaytime")),
        "playtime": to_int(item.get("maxplaytime")) or to_int(item.get("minplaytime")),
        "min_age": to_int(item.get("minage")),
        "description": item.get("description"),
        "short_description": item.get("short_description"),
        "image_url": item.get("imageurl"),
        "designers": link_names("boardgamedesigner"),
        "artists": link_names("boardgameartist"),
        "categories": link_names("boardgamecategory"),
        "mechanics": link_names("boardgamemechanic"),
        "publishers": link_names("boardgamepublisher"),
        "families": families,
        "themes": themes,
    }


def bgg_dynamicinfo(bgg_id):
    """
    /api/dynamicinfo. Returns rank, rating, weight, vote counts.
    Includes per-subdomain ranks (Strategy, Family, Party, etc.).
    """
    url = "https://api.geekdo.com/api/dynamicinfo"
    data = _retry_get_json(url, params={"objectid": bgg_id, "objecttype": "thing"})
    item = data.get("item", {}) if isinstance(data, dict) else {}
    if not item:
        return None

    stats = item.get("stats", {}) or {}
    polls = item.get("polls", {}) or {}
    weight_poll = polls.get("boardgameweight", {}) or {}

    def to_float(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def to_int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    overall_rank = None
    subdomain_ranks = {}
    for r in item.get("rankinfo", []) or []:
        rank_val = to_int(r.get("rank"))
        if r.get("rankobjecttype") == "subtype":
            overall_rank = rank_val
        else:
            sub = r.get("subdomain") or r.get("prettyname") or "?"
            subdomain_ranks[sub] = rank_val

    return {
        "bgg_rank": overall_rank,
        "subdomain_ranks": subdomain_ranks,
        "avg_rating": to_float(stats.get("average")),
        "geek_rating": to_float(stats.get("baverage")),
        "complexity": to_float(weight_poll.get("averageweight"))
                       or to_float(stats.get("avgweight")),
        "users_rated": to_int(stats.get("usersrated")),
        "weight_votes": to_int(weight_poll.get("votes")) or to_int(stats.get("numweights")),
        "num_owned": to_int(stats.get("numowned")),
        "num_plays": to_int(stats.get("numplays")),
        "language_dependence": polls.get("languagedependence"),
        "community_player_age": polls.get("playerage"),
        "best_player_count": polls.get("userplayers", {}).get("best"),
        "recommended_player_count": polls.get("userplayers", {}).get("recommended"),
    }


def bgg_fans_also_like(bgg_id):
    """Returns list of {name, bgg_id, year, rank, rating}."""
    url = "https://api.geekdo.com/api/geekitem/recs"
    data = _retry_get_json(url, params={"objectid": bgg_id, "objecttype": "thing"})
    recs = data.get("recs", []) if isinstance(data, dict) else []
    out = []
    for r in recs:
        item = r.get("item", {}) or {}
        try:
            rec_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        out.append({
            "name": item.get("name"),
            "bgg_id": rec_id,
            "year": item.get("yearpublished"),
            "rank": r.get("rank"),
            "rating": r.get("rating"),
        })
    return out


# ---------------------------------------------------------------------------
# Match scoring
# ---------------------------------------------------------------------------

def score_match(query, candidate_name):
    q = normalize_name(query)
    c = normalize_name(candidate_name)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    if q in c or c in q:
        ratio = min(len(q), len(c)) / max(len(q), len(c))
        return 0.5 + 0.4 * ratio
    return 0.0


def auto_match(query, results):
    if not results:
        return None, 0.0
    scored = sorted(
        ((score_match(query, r["name"]), r) for r in results),
        key=lambda x: -x[0],
    )
    return scored[0][1], scored[0][0]


# ---------------------------------------------------------------------------
# Per-game enrichment
# ---------------------------------------------------------------------------

def enrich_one(name, bgg_id_override=None):
    """Enrich a single game by name. Returns cache_entry."""
    search_results = []
    confidence = 1.0

    if bgg_id_override:
        bgg_id = int(bgg_id_override)
        method = "override"
    else:
        time.sleep(REQUEST_DELAY)
        search_results = bgg_search(name)
        chosen, confidence = auto_match(name, search_results)
        if chosen is None:
            return {
                "name": name,
                "bgg_id": None,
                "match_confidence": 0.0,
                "match_method": "no_results",
                "search_top_results": [],
                "fetched_at": now_iso(),
            }
        bgg_id = chosen["bgg_id"]
        method = "auto"

    time.sleep(REQUEST_DELAY)
    overview = bgg_overview(bgg_id) or {}
    time.sleep(REQUEST_DELAY)
    stats = bgg_dynamicinfo(bgg_id) or {}
    time.sleep(REQUEST_DELAY)
    fans = bgg_fans_also_like(bgg_id)

    return {
        "name": name,
        "bgg_id": bgg_id,
        "match_confidence": confidence,
        "match_method": method,
        "search_top_results": [
            {"bgg_id": r["bgg_id"], "name": r["name"], "year": r["year"]}
            for r in search_results[:5]
        ],
        "fans_also_like": fans,
        "fetched_at": now_iso(),
        **overview,
        **stats,
    }


def run_stats_patch(cache):
    """
    Re-fetch /dynamicinfo for every cached game with a bgg_id, and merge
    the result back. ~3x faster than full re-enrichment. Used to backfill
    new fields (e.g. community_player_age) without touching descriptions
    or fans_also_like.
    """
    targets = [(name, e) for name, e in cache.items() if e.get("bgg_id")]
    print(f"Patching dynamicinfo for {len(targets)} cached games...")
    for idx, (name, entry) in enumerate(targets, 1):
        try:
            time.sleep(REQUEST_DELAY)
            stats = bgg_dynamicinfo(entry["bgg_id"])
            if stats:
                entry.update(stats)
                cache[name] = entry
                save_json_atomic(CACHE_FILE, cache)
                age = stats.get("community_player_age") or "—"
                print(f"  ok  [{idx}/{len(targets)}] {name} -> age {age}")
            else:
                print(f"  ?   [{idx}/{len(targets)}] {name} -> no stats")
        except Exception as e:
            print(f"  !   [{idx}/{len(targets)}] {name} -> ERROR: {e}")
    print("Patch complete.")


def run_enrichment(games, cache, overrides, refresh_names=None):
    refresh_names = refresh_names or set()
    skipped = enriched = cached_n = failed = 0
    total = len(games)
    for idx, g in enumerate(games, 1):
        name = g["name"]
        if g["category"] in SKIP_CATEGORIES:
            skipped += 1
            continue
        if name in cache and name not in refresh_names and cache[name].get("bgg_id"):
            cached_n += 1
            continue

        prefix = f"[{idx}/{total}] {name}"
        try:
            entry = enrich_one(name, bgg_id_override=overrides.get(name))
            cache[name] = entry
            save_json_atomic(CACHE_FILE, cache)

            if entry.get("bgg_id"):
                rank = entry.get("bgg_rank") or "—"
                rating = entry.get("avg_rating")
                weight = entry.get("complexity")
                rating_s = f"{rating:.2f}" if isinstance(rating, (int, float)) else "—"
                weight_s = f"{weight:.2f}" if isinstance(weight, (int, float)) else "—"
                fans_n = len(entry.get("fans_also_like") or [])
                conf = entry.get("match_confidence", 0)
                method = entry.get("match_method", "?")
                print(f"  ok  {prefix} -> #{entry['bgg_id']} "
                      f"(rank {rank}, rating {rating_s}, wt {weight_s}, "
                      f"{fans_n} fans, conf {conf:.2f}, {method})")
                enriched += 1
            else:
                print(f"  ?   {prefix} -> NO MATCH")
                failed += 1
        except Exception as e:
            print(f"  !   {prefix} -> ERROR: {e}")
            cache[name] = {
                "name": name,
                "error": str(e),
                "fetched_at": now_iso(),
            }
            save_json_atomic(CACHE_FILE, cache)
            failed += 1

    print()
    print(f"Summary: {enriched} enriched, {cached_n} already cached, "
          f"{skipped} puzzles skipped, {failed} failed.")


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

def write_review_file(cache):
    review = []
    for name, entry in cache.items():
        confidence = entry.get("match_confidence", 0) or 0
        if entry.get("bgg_id") is None or confidence < 0.85:
            review.append({
                "name": name,
                "bgg_id": entry.get("bgg_id"),
                "matched_name": entry.get("bgg_name"),
                "confidence": confidence,
                "search_top_results": entry.get("search_top_results", []),
                "error": entry.get("error"),
            })
    review.sort(key=lambda r: (r["confidence"] or 0))
    save_json_atomic(REVIEW_FILE, review)
    print(f"Wrote {len(review)} review candidates to {REVIEW_FILE}")
    return review


def _format_player_ranges(ranges):
    """Format BGG player-count poll output [{min:3,max:4}, ...] -> '3-4' or '3,5'."""
    if not ranges:
        return ""
    parts = []
    for r in ranges:
        lo, hi = r.get("min"), r.get("max")
        if lo is None and hi is None:
            continue
        if lo == hi or hi is None:
            parts.append(str(lo))
        elif lo is None:
            parts.append(str(hi))
        else:
            parts.append(f"{lo}-{hi}" if lo != hi else str(lo))
    return ", ".join(parts)


def write_enriched_csv(games, cache, out_path=CSV_OUTPUT):
    fieldnames = [
        "row", "Game Name", "Category", "NYC",
        "BGG ID", "BGG Rank", "Avg Rating", "Geek Rating", "Weight",
        "Year", "Min Players", "Max Players",
        "Best Players", "Recommended Players",
        "Min Age (Manufacturer)", "Community Age",
        "Min Playtime", "Max Playtime",
        "Designers", "BGG Categories", "Mechanics", "Themes",
        "Fans Also Like", "Match Confidence", "Match Method", "Description",
    ]
    rows = []
    for g in games:
        e = cache.get(g["name"], {}) or {}
        fans = e.get("fans_also_like") or []
        rows.append({
            "row": g["row"],
            "Game Name": g["name"],
            "Category": g["category"],
            "NYC": g["nyc"],
            "BGG ID": e.get("bgg_id") or "",
            "BGG Rank": e.get("bgg_rank") or g.get("csv_rank") or "",
            "Avg Rating": e.get("avg_rating") or g.get("csv_rating") or "",
            "Geek Rating": e.get("geek_rating") or "",
            "Weight": e.get("complexity") or g.get("csv_weight") or "",
            "Year": e.get("year_published") or "",
            "Min Players": e.get("min_players") or "",
            "Max Players": e.get("max_players") or "",
            "Best Players": _format_player_ranges(e.get("best_player_count")),
            "Recommended Players": _format_player_ranges(e.get("recommended_player_count")),
            "Min Age (Manufacturer)": e.get("min_age") or "",
            "Community Age": e.get("community_player_age") or "",
            "Min Playtime": e.get("min_playtime") or "",
            "Max Playtime": e.get("max_playtime") or "",
            "Designers": " | ".join(e.get("designers") or []),
            "BGG Categories": " | ".join(e.get("categories") or []),
            "Mechanics": " | ".join(e.get("mechanics") or []),
            "Themes": " | ".join(e.get("themes") or []),
            "Fans Also Like": " | ".join(f["name"] for f in fans if f.get("name")),
            "Match Confidence": f"{e.get('match_confidence', 0):.2f}" if e else "",
            "Match Method": e.get("match_method") or "",
            "Description": _strip_html(e.get("description") or "")[:600],
        })
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote enriched CSV to {out_path} ({len(rows)} rows)")


def _strip_html(s):
    """BGG descriptions come with HTML tags. Strip to plain text."""
    if not s:
        return ""
    # Replace common entities
    s = (s.replace("&mdash;", "—").replace("&ndash;", "–")
           .replace("&quot;", '"').replace("&#10;", " ")
           .replace("&amp;", "&").replace("&nbsp;", " "))
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=CSV_INPUT, help="Input CSV path")
    parser.add_argument("--only", metavar="NAME",
                        help="Enrich only this exact game name")
    parser.add_argument("--refresh", metavar="NAME", action="append",
                        help="Re-fetch this game even if cached (repeatable)")
    parser.add_argument("--override", nargs=2, metavar=("NAME", "BGG_ID"),
                        help="Force a name->bgg_id mapping and persist it")
    parser.add_argument("--review", action="store_true",
                        help="Write review file and print summary; no fetching")
    parser.add_argument("--csv-out", action="store_true",
                        help="Write enriched CSV from cache; no fetching")
    parser.add_argument("--patch-stats", action="store_true",
                        help="Re-fetch only /dynamicinfo for cached games "
                             "(adds community_player_age, refreshes rank/rating)")
    args = parser.parse_args()

    games = parse_csv(args.csv)
    cache = load_json(CACHE_FILE, {})
    overrides = load_json(OVERRIDES_FILE, {})

    if args.override:
        name, bgg_id = args.override
        overrides[name] = int(bgg_id)
        save_json_atomic(OVERRIDES_FILE, overrides)
        print(f"Saved override: {name} -> {bgg_id}")
        if name in cache:
            del cache[name]
            save_json_atomic(CACHE_FILE, cache)

    if args.review:
        review = write_review_file(cache)
        for r in review[:50]:
            print(f"  conf={r['confidence']:.2f}  {r['name']!r} "
                  f"-> id={r['bgg_id']}  matched={r['matched_name']!r}")
        return

    if args.csv_out:
        write_enriched_csv(games, cache)
        return

    if args.patch_stats:
        run_stats_patch(cache)
        return

    if args.only:
        games = [g for g in games if g["name"] == args.only]
        if not games:
            print(f"No game named {args.only!r} in CSV")
            return

    refresh_names = set(args.refresh or [])
    if args.only:
        refresh_names.add(args.only)

    run_enrichment(games, cache, overrides, refresh_names=refresh_names)
    write_review_file(cache)


if __name__ == "__main__":
    main()
