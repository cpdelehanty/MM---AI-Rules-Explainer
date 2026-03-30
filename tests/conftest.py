"""
Shared pytest fixtures for Merry Meeple test suite.
Creates a temporary SQLite DB with seeded test data.
"""

import os
import sys
import sqlite3
import pytest
import tempfile
import json
from unittest.mock import MagicMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock streamlit before any app imports — app.py calls st.set_page_config at module level
if "streamlit" not in sys.modules:
    st_mock = MagicMock()
    st_mock.set_page_config = MagicMock()
    st_mock.query_params = MagicMock()
    st_mock.query_params.get = MagicMock(return_value=None)
    st_mock.cache_resource = lambda *a, **kw: (lambda f: f) if not a else a[0]
    st_mock.cache_data = lambda *a, **kw: (lambda f: f) if not a else a[0]
    st_mock.session_state = {}
    sys.modules["streamlit"] = st_mock
    sys.modules["streamlit_autorefresh"] = MagicMock()

# Mock voyageai and anthropic too (app.py imports them at top level)
if "voyageai" not in sys.modules:
    sys.modules["voyageai"] = MagicMock()
if "anthropic" not in sys.modules:
    anthropic_mock = MagicMock()
    sys.modules["anthropic"] = anthropic_mock


@pytest.fixture(autouse=True)
def test_db(tmp_path, monkeypatch):
    """Create a temporary DB, initialize schema, seed test data, and patch DB_PATH."""
    db_file = str(tmp_path / "test_game_library.db")

    # Patch DB_PATH in all modules
    import database
    monkeypatch.setattr(database, "DB_PATH", db_file)

    # Also patch any module that imports DB_PATH
    for mod_name in ["sync_deals", "app", "admin"]:
        try:
            mod = __import__(mod_name)
            if hasattr(mod, "DB_PATH"):
                monkeypatch.setattr(mod, "DB_PATH", db_file)
        except (ImportError, AttributeError):
            pass

    # Initialize schema
    database.init_database()

    # Seed test data
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    # --- Menu items ---
    menu_items = [
        ("beer_pbr", "Beer - Draft", "PBR", "", "$6", "", 1),
        ("beer_ipa", "Beer - Draft", "IPA", "", "$8", "", 1),
        ("wine_red", "Wine", "House Red", "", "$10", "", 1),
        ("snack_pretzel", "Snacks", "Pretzel Bites", "", "$10", "vegetarian", 1),
        ("snack_olives", "Snacks", "Marinated Olives", "", "$8", "vegan", 1),
        ("snack_cheese", "Snacks", "Cheese Plate", "", "$12", "vegetarian", 1),
        ("share_flatbread_m", "Shareables", "Flatbread \u2014 Margherita", "", "$14", "vegetarian", 1),
        ("share_flatbread_w", "Shareables", "Flatbread \u2014 White", "", "$14", "", 1),
        ("sweet_brownie", "Sweets", "Brownie", "", "$6", "vegetarian", 1),
        ("popcorn_classic", "Popcorn", "Classic Popcorn", "", "$5", "vegan", 1),
        ("coffee_latte", "Non-Alc", "Latte", "", "$5", "", 1),
    ]
    for item_id, cat, name, desc, price, tags, avail in menu_items:
        cursor.execute("""
            INSERT INTO menu_items (item_id, category, name, description, price, dietary_tags, available)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (item_id, cat, name, desc, price, tags, avail))

    # --- Deals ---
    deals = [
        ("HAPPY_HOUR", "Happy Hour", "20% off all drinks 4-7pm!", "percent", 20, None, None, None,
         15.0, 0, 1, 0, "16:00", "19:00", None, 1, None),
        ("FIRST_VISIT", "Welcome Deal", "10% off your first visit!", "percent", 10, None, None, None,
         0, 0, 1, 1, None, None, None, 1, None),
        ("FREE_POPCORN", "Free Popcorn", "Free popcorn with $20+ order!", "free_item", 0,
         "Classic Popcorn", "popcorn_classic", None,
         20.0, 0, 1, 0, None, None, None, 1, None),
        ("EXPIRED_DEAL", "Old Deal", "This deal expired!", "percent", 50, None, None, None,
         0, 0, 1, 0, None, None, None, 1, "2025-01-01"),
        ("WEEKEND_ONLY", "Weekend Special", "15% off on weekends!", "percent", 15, None, None, None,
         0, 0, 1, 0, None, None, "Sat,Sun", 1, None),
    ]
    for d in deals:
        cursor.execute("""
            INSERT INTO deals (deal_id, name, display_text, discount_type, discount_value,
                free_item_description, free_item_id, target_category,
                min_spend, min_visit_count, min_party_size, first_visit_only,
                time_of_day_start, time_of_day_end, days_of_week, active, expiry_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, d)

    # --- Auto deal rules ---
    cursor.execute("""
        INSERT INTO auto_deal_rules (rule_id, name, min_spend_threshold, discount_percent,
            max_discount, display_template, active)
        VALUES ('AUTO_25', 'Spend $25 deal', 25.0, 10.0, 5.0,
                'Spend ${gap} more to get 10% off (up to $5 off)!', 1)
    """)

    # --- Cart upsell rules ---
    upsells = [
        ("UPSELL_BEER_SNACK", "Beer + Snack", "Beer - Draft", "Snacks,Shareables",
         2, 0, 0, 0, 0, "Snacks", 25.0,
         "Great with beer - add a snack for 25% off!", "Pretzel Bites,Marinated Olives", 1),
        ("UPSELL_GROUP_FLAT", "Group Flatbread", "Snacks,Popcorn", "Shareables",
         1, 3, 0, 15.0, 0, "Shareables", 20.0,
         "Feeding the table? Add a flatbread for 20% off!",
         "Flatbread \u2014 Margherita,Flatbread \u2014 White", 4),
    ]
    for u in upsells:
        cursor.execute("""
            INSERT INTO cart_upsells (upsell_id, name, requires_categories, excludes_categories,
                min_requires_count, min_items, max_items, min_subtotal, max_subtotal,
                target_category, discount_percent, message, suggested_items, priority, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, u)

    # --- Seed tables ---
    for i in range(1, 13):
        cursor.execute("INSERT OR IGNORE INTO tables (table_number) VALUES (?)", (i,))

    conn.commit()
    conn.close()

    yield db_file
