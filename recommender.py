"""
Recommendation engine for The Merry Meeple cafe.

Pure functions that read from the cafe_games table and return ranked
recommendations. No Streamlit imports here — the UI layer lives in
browse_ui.py and calls these functions.

Key functions:
- get_all_games()                     all cafe games (for filters)
- filter_games(...)                   apply party-size/age/category/etc.
- find_similar(anchor_name, ...)      "Something like X" hybrid score
- top_categories/themes/mechanics()   for chip suggestions in the UI
- unrated_played_games(user_id)       prompt-able rows from prior visits
- popular_themes_for_user(user_id)    bias future suggestions
"""

import json
import re
import sqlite3
from collections import Counter

from database_recs import DB_PATH


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _row_to_game(row):
    """Hydrate a cafe_games row dict into a fully-typed game dict.

    JSON fields are parsed; missing values become empty lists / None.
    """
    g = dict(row)
    for col in ("categories", "mechanics", "themes", "designers",
                "publishers", "best_player_count", "recommended_player_count",
                "fans_also_like", "subdomain_ranks", "cafe_categories"):
        v = g.get(col)
        if isinstance(v, str) and v.strip():
            try:
                g[col] = json.loads(v)
            except json.JSONDecodeError:
                g[col] = []
        elif v is None:
            g[col] = [] if col != "subdomain_ranks" else {}
    return g


def get_all_games(in_stock_only=True, with_bgg_only=False):
    """Return all cafe games as a list of fully-typed dicts."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    sql = "SELECT * FROM cafe_games"
    where = []
    if in_stock_only:
        where.append("in_stock = 1")
    if with_bgg_only:
        where.append("bgg_id IS NOT NULL")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY name"
    cur.execute(sql)
    games = [_row_to_game(r) for r in cur.fetchall()]
    conn.close()
    return games


def get_game_by_name(name):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM cafe_games WHERE name = ?", (name,))
    row = cur.fetchone()
    conn.close()
    return _row_to_game(row) if row else None


def get_game_by_id(game_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM cafe_games WHERE game_id = ?", (game_id,))
    row = cur.fetchone()
    conn.close()
    return _row_to_game(row) if row else None


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

# Family filter ⇄ community age range
FAMILY_FILTERS = {
    "family":   {"max_age": 8,  "label": "Family-friendly"},
    "all":      {"max_age": None, "label": "All audiences"},
    "adult":    {"min_age": 14, "label": "Adults only"},
}


def _community_age_int(s):
    """Parse '10+', '8–10', '12' -> a single representative integer (lower bound)."""
    if not s:
        return None
    m = re.match(r"^\s*(\d+)", str(s))
    return int(m.group(1)) if m else None


def player_count_supported(game, party_size):
    """True if the game's min/max players (with poll preferences) covers party_size."""
    if party_size is None:
        return True
    minp = game.get("min_players")
    maxp = game.get("max_players")
    if minp is None or maxp is None:
        return True  # don't filter out games with missing data
    return minp <= party_size <= maxp


def passes_family_filter(game, family_filter):
    if not family_filter or family_filter == "all":
        return True
    age = _community_age_int(game.get("community_player_age")) \
        or game.get("min_age_manufacturer")
    if age is None:
        return True  # don't filter out games with missing data
    spec = FAMILY_FILTERS.get(family_filter, {})
    if "max_age" in spec and spec["max_age"] is not None:
        return age <= spec["max_age"]
    if "min_age" in spec and spec["min_age"] is not None:
        return age >= spec["min_age"]
    return True


def filter_games(games, party_size=None, family_filter=None,
                 cafe_category=None, bgg_category=None, mechanic=None,
                 theme=None, in_stock_only=True):
    """Apply zero or more filter dimensions. Empty filters are no-ops."""
    out = []
    for g in games:
        if in_stock_only and not g.get("in_stock"):
            continue
        if not player_count_supported(g, party_size):
            continue
        if not passes_family_filter(g, family_filter):
            continue
        if cafe_category and cafe_category not in (g.get("cafe_categories") or []):
            continue
        if bgg_category and bgg_category not in (g.get("categories") or []):
            continue
        if mechanic and mechanic not in (g.get("mechanics") or []):
            continue
        if theme:
            game_themes = [t for t in (g.get("themes") or []) if is_thematic(t)]
            if theme not in game_themes:
                continue
        out.append(g)
    return out


# ---------------------------------------------------------------------------
# Chip suggestions — most-represented categories/themes/mechanics
# ---------------------------------------------------------------------------

def top_cafe_categories(games=None, limit=8):
    """Cafe library categories — preserve the canonical ordering used in the CSV."""
    if games is None:
        games = get_all_games()
    counter = Counter()
    for g in games:
        for c in (g.get("cafe_categories") or []):
            counter[c] += 1
    return [c for c, _ in counter.most_common(limit)]


def top_bgg_categories(games=None, limit=8):
    if games is None:
        games = get_all_games()
    counter = Counter()
    for g in games:
        for c in (g.get("categories") or []):
            counter[c] += 1
    return [c for c, _ in counter.most_common(limit)]


def top_mechanics(games=None, limit=8):
    if games is None:
        games = get_all_games()
    counter = Counter()
    for g in games:
        for m in (g.get("mechanics") or []):
            counter[m] += 1
    return [m for m, _ in counter.most_common(limit)]


# BGG family prefixes that aren't thematic (production / metadata noise)
NON_THEMATIC_PREFIXES = (
    "Crowdfunding:", "Category:", "Containers:", "Promotional:",
    "Series:", "Word Games:", "Brands:", "Country:",
    "Components:", "Mechanism:", "Players:", "Game:", "Misc:", "Admin:",
    "Digital Implementations:",
)


def is_thematic(family_str):
    if not family_str:
        return False
    return not family_str.startswith(NON_THEMATIC_PREFIXES)


def top_themes(games=None, limit=8):
    """Top themes across the library, filtered to actually-thematic entries."""
    if games is None:
        games = get_all_games()
    counter = Counter()
    for g in games:
        for t in (g.get("themes") or []):
            if is_thematic(t):
                counter[t] += 1
    return [t for t, _ in counter.most_common(limit)]


# ---------------------------------------------------------------------------
# "Something like X" — hybrid scoring
# ---------------------------------------------------------------------------

def jaccard(a, b):
    sa, sb = set(a or []), set(b or [])
    if not sa and not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def complexity_proximity(a, b):
    """1.0 if equal, 0.0 if 4 points apart on BGG's 1-5 weight scale."""
    if a is None or b is None:
        return 0.5  # neutral when missing
    return max(0.0, 1.0 - abs(a - b) / 4.0)


# ---------------------------------------------------------------------------
# Centralized ranking — every recommendation path runs candidates through this
# ---------------------------------------------------------------------------

# Tunable weights. Higher = bigger influence on final ordering.
RANK_WEIGHTS = {
    "geek_rating":              1.0,   # base signal
    "best_player_match":        0.5,   # community vote: best at this count
    "recommended_player_match": 0.25,  # community vote: recommended at this count
    "user_theme_match":         0.4,   # Phase 2: themes the user has thumbed-up
    "user_mechanic_match":      0.4,   # Phase 2: mechanics the user has thumbed-up
    "anchor_similarity":        2.0,   # weight applied to score_like in like-X path
}


def _player_count_in(ranges, party_size):
    """True if `party_size` is covered by any {min,max} range in `ranges`."""
    if not ranges or party_size is None:
        return False
    for r in ranges:
        if not isinstance(r, dict):
            continue
        lo, hi = r.get("min"), r.get("max")
        if lo is None and hi is None:
            continue
        lo = lo if lo is not None else hi
        hi = hi if hi is not None else lo
        if lo <= party_size <= hi:
            return True
    return False


def rank_score(game, party_size=None, user_themes=None, user_mechanics=None,
               anchor_similarity=0.0):
    """
    Composite ranking score for a single game in a given context.

    Without any context (party_size=None, no user data, no anchor), this
    reduces to a normalized geek-rating sort. Each context dimension layers
    on additional weight.
    """
    geek = (game.get("geek_rating") or 0) / 10.0  # roughly 0.55 - 0.85
    score = RANK_WEIGHTS["geek_rating"] * geek

    if party_size is not None:
        # Best > recommended > neither. Don't double-count.
        if _player_count_in(game.get("best_player_count"), party_size):
            score += RANK_WEIGHTS["best_player_match"]
        elif _player_count_in(game.get("recommended_player_count"), party_size):
            score += RANK_WEIGHTS["recommended_player_match"]

    if user_themes:
        overlap = len(set(game.get("themes") or []) & set(user_themes))
        score += min(overlap / 3.0, 1.0) * RANK_WEIGHTS["user_theme_match"]

    if user_mechanics:
        overlap = len(set(game.get("mechanics") or []) & set(user_mechanics))
        score += min(overlap / 3.0, 1.0) * RANK_WEIGHTS["user_mechanic_match"]

    score += RANK_WEIGHTS["anchor_similarity"] * anchor_similarity
    return score


def rank_games(games, party_size=None, user_themes=None, user_mechanics=None):
    """Sort `games` in place by `rank_score` descending. Returns the list."""
    games.sort(
        key=lambda g: -rank_score(g, party_size=party_size,
                                   user_themes=user_themes,
                                   user_mechanics=user_mechanics),
    )
    return games


def score_like(anchor, candidate):
    """Hybrid similarity: BGG fans-also-like (60%) + content overlap (40%).

    Within content (40%):
       0.5 jaccard(mechanics)
       0.3 jaccard(categories+themes)
       0.2 complexity_proximity
    """
    if candidate.get("game_id") == anchor.get("game_id"):
        return 0.0  # never recommend the same game

    fans = anchor.get("fans_also_like") or []
    fan_score = 0.0
    for i, f in enumerate(fans):
        if f.get("bgg_id") == candidate.get("bgg_id"):
            # Position 0 -> 1.0, last -> ~0.3
            fan_score = 1.0 - (i / max(1, len(fans))) * 0.7
            break

    a_cats = (anchor.get("categories") or []) + (anchor.get("themes") or [])
    c_cats = (candidate.get("categories") or []) + (candidate.get("themes") or [])

    content = (
        0.5 * jaccard(anchor.get("mechanics") or [], candidate.get("mechanics") or [])
        + 0.3 * jaccard(a_cats, c_cats)
        + 0.2 * complexity_proximity(anchor.get("complexity"),
                                     candidate.get("complexity"))
    )

    return 0.6 * fan_score + 0.4 * content


def find_similar(anchor_name, party_size=None, family_filter=None,
                 user_themes=None, user_mechanics=None,
                 limit=6, all_games=None):
    """
    Top-N games similar to `anchor_name`, after applying filters and ranking
    via the central `rank_score` (anchor similarity + best-player bonus +
    user-pref boosts).
    """
    if all_games is None:
        all_games = get_all_games()
    anchor = next((g for g in all_games if g["name"] == anchor_name), None)
    if anchor is None or anchor.get("bgg_id") is None:
        return None, []

    candidates = filter_games(
        [g for g in all_games if g["game_id"] != anchor["game_id"]],
        party_size=party_size, family_filter=family_filter,
    )
    scored = []
    for c in candidates:
        sim = score_like(anchor, c)
        if sim <= 0:
            continue  # only consider games with non-zero anchor similarity
        rs = rank_score(
            c, party_size=party_size,
            user_themes=user_themes, user_mechanics=user_mechanics,
            anchor_similarity=sim,
        )
        scored.append((rs, c))
    scored.sort(key=lambda x: -x[0])
    return anchor, [c for _, c in scored[:limit]]


def fuzzy_find_anchor(query, all_games=None):
    """
    Find candidate anchor games by user-typed query. Lenient matching:
    contains-substring on the normalized name, return up to 8 hits.
    """
    if all_games is None:
        all_games = get_all_games()
    q = re.sub(r"[^a-z0-9]+", "", query.lower())
    if not q:
        return []
    hits = []
    for g in all_games:
        n = re.sub(r"[^a-z0-9]+", "", g["name"].lower())
        if q == n:
            hits.insert(0, (1.0, g))
        elif q in n or n in q:
            hits.append((min(len(q), len(n)) / max(len(q), len(n)), g))
    hits.sort(key=lambda x: -x[0])
    return [g for _, g in hits[:8]]


# ---------------------------------------------------------------------------
# Personalization (Phase 2 — minimal implementations now, expanded later)
# ---------------------------------------------------------------------------

def unrated_played_games(user_id):
    """
    Games the user picked (recommendation_log.was_selected=1) but hasn't
    rated yet. Returns list of cafe_games rows, most recent first.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT cg.*, MAX(rl.recommended_at) AS last_picked
        FROM cafe_games cg
        JOIN recommendation_log rl ON rl.recommended_game_id = cg.game_id
        WHERE rl.user_id = ? AND rl.was_selected = 1
          AND NOT EXISTS (
              SELECT 1 FROM ratings r
              WHERE r.user_id = rl.user_id
                AND r.game_id = rl.recommended_game_id
                AND r.rated_at >= rl.recommended_at
          )
        GROUP BY cg.game_id
        ORDER BY last_picked DESC
    """, (user_id,))
    games = [_row_to_game(r) for r in cur.fetchall()]
    conn.close()
    return games


def popular_themes_for_user(user_id, limit=5):
    """
    Themes from games the user thumbed-up. Empty for new users (Phase 1
    fallback to global popular themes).
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT cg.themes
        FROM ratings r JOIN cafe_games cg ON cg.game_id = r.game_id
        WHERE r.user_id = ? AND r.rating = 1 AND cg.themes IS NOT NULL
    """, (user_id,))
    counter = Counter()
    for (themes_json,) in cur.fetchall():
        try:
            themes = json.loads(themes_json) or []
        except json.JSONDecodeError:
            continue
        for t in themes:
            counter[t] += 1
    conn.close()
    return [t for t, _ in counter.most_common(limit)]
