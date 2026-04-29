"""
Unit tests for the pure recommender functions.

Uses an in-memory cafe_games table seeded with synthetic data — fast,
deterministic, and independent of the production bgg_cache.
"""

import json
import sqlite3

import pytest

import database_recs
import recommender


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

SEED = [
    # name, bgg_id, complexity, geek_rating, avg_rating, bgg_rank,
    # min_players, max_players, playtime, min_age, community_age,
    # categories, mechanics, themes, fans_also_like, cafe_categories,
    # best, recommended
    {
        "name": "Catan", "bgg_id": 13, "complexity": 2.28, "geek_rating": 6.90,
        "avg_rating": 7.09, "bgg_rank": 617, "min_players": 3, "max_players": 4,
        "playtime": 90, "min_age": 10, "community_age": "10+",
        "categories": ["Economic", "Negotiation"],
        "mechanics": ["Dice Rolling", "Hand Management", "Trading"],
        "themes": ["Theme: Colonial"],
        "fans": [
            {"bgg_id": 822, "name": "Carcassonne"},
            {"bgg_id": 30549, "name": "Pandemic"},
        ],
        "cafe_categories": ["Mid-Weight Strategy"],
        "best": [{"min": 4, "max": 4}],
        "recommended": [{"min": 3, "max": 4}],
    },
    {
        "name": "Carcassonne", "bgg_id": 822, "complexity": 1.89,
        "geek_rating": 7.41, "avg_rating": 7.42, "bgg_rank": 239,
        "min_players": 2, "max_players": 5, "playtime": 45,
        "min_age": 7, "community_age": "8+",
        "categories": ["Medieval", "Territory Building"],
        "mechanics": ["Tile Placement", "Hand Management"],
        "themes": ["Theme: Medieval"],
        "fans": [
            {"bgg_id": 13, "name": "Catan"},
            {"bgg_id": 9209, "name": "Ticket to Ride"},
        ],
        "cafe_categories": ["Gateway Strategy"],
        "best": [{"min": 2, "max": 2}],
        "recommended": [{"min": 2, "max": 5}],
    },
    {
        "name": "Pandemic", "bgg_id": 30549, "complexity": 2.39,
        "geek_rating": 7.51, "avg_rating": 7.51, "bgg_rank": 171,
        "min_players": 2, "max_players": 4, "playtime": 45,
        "min_age": 8, "community_age": "10+",
        "categories": ["Medical"],
        "mechanics": ["Hand Management", "Action Points"],
        "themes": ["Theme: Disease"],
        "fans": [
            {"bgg_id": 13, "name": "Catan"},
        ],
        "cafe_categories": ["Cooperative", "Mid-Weight Strategy"],
        "best": [{"min": 4, "max": 4}],
        "recommended": [{"min": 2, "max": 4}],
    },
    {
        "name": "Cards Against Humanity", "bgg_id": 50381, "complexity": 1.17,
        "geek_rating": 5.71, "avg_rating": 5.71, "bgg_rank": 15336,
        "min_players": 4, "max_players": 30, "playtime": 60,
        "min_age": 17, "community_age": "18+",
        "categories": ["Humor", "Party Game"],
        "mechanics": ["Voting"],
        "themes": [],
        "fans": [],
        "cafe_categories": ["Party"],
        "best": [{"min": 8, "max": 8}],
        "recommended": [{"min": 6, "max": 12}],
    },
    {
        "name": "First Orchard", "bgg_id": 41302, "complexity": 1.0,
        "geek_rating": 5.5, "avg_rating": 6.68, "bgg_rank": 3056,
        "min_players": 1, "max_players": 4, "playtime": 10,
        "min_age": 2, "community_age": "3+",
        "categories": ["Children's Game"],
        "mechanics": ["Cooperative Game", "Dice Rolling"],
        "themes": ["Animals: Birds"],
        "fans": [],
        "cafe_categories": ["Kids"],
        "best": None,
        "recommended": None,
    },
]


@pytest.fixture(autouse=True)
def seeded_db(tmp_path, monkeypatch):
    """Replace DB_PATH with a temp DB and seed cafe_games with SEED data."""
    db_file = str(tmp_path / "test_recommender.db")
    monkeypatch.setattr(database_recs, "DB_PATH", db_file)
    monkeypatch.setattr(recommender, "DB_PATH", db_file)
    database_recs.init_recommendation_tables()

    # Add the extra columns the sync script normally creates (subset of those used here)
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    extras = [
        ("min_age_manufacturer", "INTEGER"),
        ("community_player_age", "TEXT"),
        ("cafe_categories", "TEXT"),
        ("fans_also_like", "TEXT"),
        ("description", "TEXT"),
        ("image_url", "TEXT"),
        ("bgg_url", "TEXT"),
        ("min_playtime", "INTEGER"),
        ("max_playtime", "INTEGER"),
        ("best_player_count", "TEXT"),
        ("recommended_player_count", "TEXT"),
    ]
    for col, typ in extras:
        try:
            cur.execute(f"ALTER TABLE cafe_games ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass  # already exists

    for s in SEED:
        cur.execute("""
            INSERT INTO cafe_games (
                name, bgg_id, complexity, geek_rating, avg_rating, bgg_rank,
                min_players, max_players, playtime,
                categories, mechanics, themes, fans_also_like,
                min_age_manufacturer, community_player_age, cafe_categories,
                in_stock, bgg_url,
                best_player_count, recommended_player_count
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            s["name"], s["bgg_id"], s["complexity"], s["geek_rating"],
            s["avg_rating"], s["bgg_rank"],
            s["min_players"], s["max_players"], s["playtime"],
            json.dumps(s["categories"]), json.dumps(s["mechanics"]),
            json.dumps(s["themes"]), json.dumps(s["fans"]),
            s["min_age"], s["community_age"], json.dumps(s["cafe_categories"]),
            1, f"https://boardgamegeek.com/boardgame/{s['bgg_id']}",
            json.dumps(s["best"]) if s["best"] else None,
            json.dumps(s["recommended"]) if s["recommended"] else None,
        ))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def test_get_all_games_loads_seed():
    games = recommender.get_all_games()
    assert len(games) == len(SEED)
    names = {g["name"] for g in games}
    assert names == {s["name"] for s in SEED}


def test_json_columns_are_parsed():
    g = recommender.get_game_by_name("Catan")
    assert g["categories"] == ["Economic", "Negotiation"]
    assert g["mechanics"] == ["Dice Rolling", "Hand Management", "Trading"]
    assert g["fans_also_like"][0]["name"] == "Carcassonne"
    assert g["cafe_categories"] == ["Mid-Weight Strategy"]


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

def test_party_size_filter_excludes_too_small():
    games = recommender.get_all_games()
    # Catan needs 3-4; party of 2 should exclude it
    res = recommender.filter_games(games, party_size=2)
    names = {g["name"] for g in res}
    assert "Catan" not in names
    assert "Carcassonne" in names  # 2-5 — supports 2


def test_party_size_filter_includes_in_range():
    games = recommender.get_all_games()
    res = recommender.filter_games(games, party_size=4)
    names = {g["name"] for g in res}
    assert "Catan" in names
    assert "Pandemic" in names


def test_family_filter_excludes_adults_only():
    games = recommender.get_all_games()
    res = recommender.filter_games(games, family_filter="family")
    names = {g["name"] for g in res}
    assert "Cards Against Humanity" not in names
    assert "First Orchard" in names  # community_age 3+


def test_adult_filter_only_returns_mature_games():
    games = recommender.get_all_games()
    res = recommender.filter_games(games, family_filter="adult")
    names = {g["name"] for g in res}
    assert "Cards Against Humanity" in names
    assert "First Orchard" not in names


def test_all_filter_returns_everything():
    games = recommender.get_all_games()
    res = recommender.filter_games(games, family_filter="all")
    assert len(res) == len(games)


def test_cafe_category_filter():
    games = recommender.get_all_games()
    res = recommender.filter_games(games, cafe_category="Cooperative")
    names = {g["name"] for g in res}
    assert names == {"Pandemic"}


def test_cafe_category_filter_with_multi_category_game():
    """Pandemic is in both Cooperative and Mid-Weight Strategy."""
    games = recommender.get_all_games()
    res1 = recommender.filter_games(games, cafe_category="Cooperative")
    res2 = recommender.filter_games(games, cafe_category="Mid-Weight Strategy")
    assert "Pandemic" in {g["name"] for g in res1}
    assert "Pandemic" in {g["name"] for g in res2}


def test_combined_filters():
    games = recommender.get_all_games()
    res = recommender.filter_games(
        games, party_size=3, family_filter="family",
        mechanic="Hand Management",
    )
    names = {g["name"] for g in res}
    # Catan: 3-4 players, age 10+, has Hand Management — but family means age <=8
    # Catan excluded by family filter (age 10), Pandemic excluded same way (10)
    # Carcassonne: age 8 OK, 2-5 players, has Hand Management
    assert names == {"Carcassonne"}


# ---------------------------------------------------------------------------
# Top categories / themes / mechanics
# ---------------------------------------------------------------------------

def test_top_cafe_categories_ordered_by_count():
    cats = recommender.top_cafe_categories(limit=5)
    # Mid-Weight Strategy appears in Catan + Pandemic = 2 hits
    assert cats[0] == "Mid-Weight Strategy"


def test_top_themes_filters_administrative_prefixes():
    # Add a game with a non-thematic family
    conn = sqlite3.connect(database_recs.DB_PATH)
    conn.execute("""
        UPDATE cafe_games SET themes = ?
        WHERE name = 'Catan'
    """, (json.dumps(["Theme: Colonial", "Crowdfunding: Kickstarter"]),))
    conn.commit()
    conn.close()

    themes = recommender.top_themes()
    assert any("Colonial" in t for t in themes)
    assert not any("Crowdfunding" in t for t in themes)


# ---------------------------------------------------------------------------
# Like-X scoring
# ---------------------------------------------------------------------------

def test_score_like_excludes_self():
    games = recommender.get_all_games()
    catan = next(g for g in games if g["name"] == "Catan")
    assert recommender.score_like(catan, catan) == 0.0


def test_score_like_high_for_fans_match():
    games = recommender.get_all_games()
    catan = next(g for g in games if g["name"] == "Catan")
    carcassonne = next(g for g in games if g["name"] == "Carcassonne")
    # Carcassonne is in Catan's fans_also_like at position 0
    score = recommender.score_like(catan, carcassonne)
    # 0.6 * 1.0 (top fan) + 0.4 * content > 0.6
    assert score > 0.6


def test_score_like_lower_for_unrelated():
    games = recommender.get_all_games()
    catan = next(g for g in games if g["name"] == "Catan")
    cah = next(g for g in games if g["name"] == "Cards Against Humanity")
    score = recommender.score_like(catan, cah)
    assert score < 0.3


def test_find_similar_returns_anchor_and_results():
    anchor, similar = recommender.find_similar("Catan", party_size=4, limit=3)
    assert anchor is not None
    assert anchor["name"] == "Catan"
    names = [g["name"] for g in similar]
    # Carcassonne and Pandemic are in Catan's fans
    assert "Carcassonne" in names or "Pandemic" in names
    assert "Catan" not in names  # anchor never recommends itself


def test_find_similar_returns_none_for_unknown():
    anchor, similar = recommender.find_similar("Nonexistent Game")
    assert anchor is None
    assert similar == []


def test_fuzzy_find_anchor_substring_match():
    hits = recommender.fuzzy_find_anchor("catan")
    assert any(g["name"] == "Catan" for g in hits)


def test_fuzzy_find_anchor_no_match():
    hits = recommender.fuzzy_find_anchor("xxxxxxx")
    assert hits == []


# ---------------------------------------------------------------------------
# Personalization helpers
# ---------------------------------------------------------------------------

def test_unrated_played_games_empty_for_new_user():
    user = database_recs.create_or_get_user("+15555550000")
    games = recommender.unrated_played_games(user["user_id"])
    assert games == []


def test_unrated_played_games_returns_picked_unrated():
    """User picked Catan but hasn't rated it yet."""
    user = database_recs.create_or_get_user("+15555550001")
    session_id = database_recs.create_session(user["user_id"])
    catan = recommender.get_game_by_name("Catan")
    database_recs.log_recommendation(
        session_id=session_id, user_id=user["user_id"],
        game_id=catan["game_id"], score=0.9, breakdown={}, version="t", position=1,
    )
    database_recs.mark_recommendation_selected(session_id, catan["game_id"])

    games = recommender.unrated_played_games(user["user_id"])
    assert len(games) == 1
    assert games[0]["name"] == "Catan"



# ---------------------------------------------------------------------------
# rank_score — best-player bonus + ordering
# ---------------------------------------------------------------------------

def test_rank_score_with_no_context_uses_geek_rating():
    """With no party_size/user prefs, a higher geek-rated game ranks higher."""
    games = recommender.get_all_games()
    pandemic = next(g for g in games if g["name"] == "Pandemic")  # geek 7.51
    catan = next(g for g in games if g["name"] == "Catan")        # geek 6.90
    assert recommender.rank_score(pandemic) > recommender.rank_score(catan)


def test_rank_score_best_player_match_adds_bonus():
    """A 4-player party gets a bonus on Catan (best=4) over its base score."""
    games = recommender.get_all_games()
    catan = next(g for g in games if g["name"] == "Catan")
    base = recommender.rank_score(catan, party_size=None)
    boosted = recommender.rank_score(catan, party_size=4)
    assert boosted > base
    # Within ~0.55 of the best_player_match weight (geek normalization noise).
    diff = boosted - base
    assert diff >= recommender.RANK_WEIGHTS["best_player_match"] * 0.9


def test_rank_score_recommended_falls_back_when_not_best():
    """Pandemic is best at 4 but recommended for 2-4. A 3-player party hits
    the recommended bonus, not the best bonus."""
    games = recommender.get_all_games()
    pandemic = next(g for g in games if g["name"] == "Pandemic")
    base = recommender.rank_score(pandemic)
    rec_only = recommender.rank_score(pandemic, party_size=3)
    best = recommender.rank_score(pandemic, party_size=4)
    assert base < rec_only < best


def test_rank_score_no_player_data_no_bonus():
    """First Orchard has no best/recommended data — party_size adds nothing."""
    games = recommender.get_all_games()
    orchard = next(g for g in games if g["name"] == "First Orchard")
    base = recommender.rank_score(orchard)
    boosted = recommender.rank_score(orchard, party_size=2)
    assert base == boosted


def test_rank_games_reorders_by_party_size_match():
    """At party=4, Catan (best=4) should rank above Carcassonne (best=2)
    even though Carcassonne has a higher geek_rating without context."""
    games = recommender.get_all_games()
    # Sanity — without context Carcassonne wins on geek
    no_context = recommender.rank_games(list(games))
    car_idx_no = next(i for i, g in enumerate(no_context) if g["name"] == "Carcassonne")
    cat_idx_no = next(i for i, g in enumerate(no_context) if g["name"] == "Catan")
    assert car_idx_no < cat_idx_no

    # With party=4, Catan's best-match bonus (+0.5) overcomes the ~0.05 geek gap
    with_context = recommender.rank_games(list(games), party_size=4)
    car_idx = next(i for i, g in enumerate(with_context) if g["name"] == "Carcassonne")
    cat_idx = next(i for i, g in enumerate(with_context) if g["name"] == "Catan")
    assert cat_idx < car_idx


def test_rank_score_user_theme_match_boosts():
    """A user who likes 'Theme: Medieval' boosts Carcassonne over Catan."""
    games = recommender.get_all_games()
    catan = next(g for g in games if g["name"] == "Catan")
    carcassonne = next(g for g in games if g["name"] == "Carcassonne")
    no_pref = recommender.rank_score(carcassonne) - recommender.rank_score(catan)
    with_pref = (
        recommender.rank_score(carcassonne, user_themes=["Theme: Medieval"])
        - recommender.rank_score(catan, user_themes=["Theme: Medieval"])
    )
    assert with_pref > no_pref  # gap widens in Carcassonne's favor


def test_unrated_played_games_excludes_rated():
    user = database_recs.create_or_get_user("+15555550002")
    session_id = database_recs.create_session(user["user_id"])
    catan = recommender.get_game_by_name("Catan")
    database_recs.log_recommendation(
        session_id=session_id, user_id=user["user_id"],
        game_id=catan["game_id"], score=0.9, breakdown={}, version="t", position=1,
    )
    database_recs.mark_recommendation_selected(session_id, catan["game_id"])
    database_recs.save_rating(
        user["user_id"], catan["game_id"], rating=1, session_id=session_id,
    )
    games = recommender.unrated_played_games(user["user_id"])
    assert games == []
