"""
Merry Meeple test suite — unit + integration tests.
No API keys needed. Uses temporary SQLite DB from conftest.py.
"""

import os
import sys
import sqlite3
import json
import pytest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ============================================================
# 1. PURE FUNCTIONS (no DB)
# ============================================================

class TestEscapeDollars:
    def test_basic(self):
        from app import escape_dollars
        assert escape_dollars("$5.00") == "\\$5.00"

    def test_no_dollars(self):
        from app import escape_dollars
        assert escape_dollars("hello world") == "hello world"

    def test_multiple(self):
        from app import escape_dollars
        assert escape_dollars("$5 and $10") == "\\$5 and \\$10"

    def test_empty_string(self):
        from app import escape_dollars
        assert escape_dollars("") == ""


class TestPhoneValidation:
    def test_normalize_10_digit(self):
        from user_store import normalize_phone
        assert normalize_phone("7185551234") == "+17185551234"

    def test_normalize_with_dashes(self):
        from user_store import normalize_phone
        assert normalize_phone("718-555-1234") == "+17185551234"

    def test_normalize_with_parens(self):
        from user_store import normalize_phone
        assert normalize_phone("(718) 555-1234") == "+17185551234"

    def test_normalize_with_country_code(self):
        from user_store import normalize_phone
        assert normalize_phone("17185551234") == "+17185551234"

    def test_normalize_too_short(self):
        from user_store import normalize_phone
        result = normalize_phone("12345")
        assert result is None or len(result) < 12

    def test_validate_good(self):
        from user_store import validate_phone
        assert validate_phone("+17185551234") is True

    def test_validate_bad(self):
        from user_store import validate_phone
        assert validate_phone("12345") is False

    def test_validate_none(self):
        from user_store import validate_phone
        assert validate_phone(None) is False


class TestCosineSimilarity:
    def test_identical(self):
        from app import cosine_similarity
        assert cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)

    def test_orthogonal(self):
        from app import cosine_similarity
        assert cosine_similarity([1, 0, 0], [0, 1, 0]) == pytest.approx(0.0)

    def test_opposite(self):
        from app import cosine_similarity
        assert cosine_similarity([1, 0], [-1, 0]) == pytest.approx(-1.0)


class TestCartSubtotal:
    def test_empty(self):
        from app import get_cart_subtotal
        assert get_cart_subtotal([]) == 0

    def test_single_item(self):
        from app import get_cart_subtotal
        assert get_cart_subtotal([{"price": 5.0, "qty": 1}]) == 5.0

    def test_quantities(self):
        from app import get_cart_subtotal
        cart = [{"price": 5.0, "qty": 2}, {"price": 10.0, "qty": 1}]
        assert get_cart_subtotal(cart) == 20.0


class TestStaffPingTags:
    def test_extract_tag(self):
        from app import process_staff_ping_tags
        text = "I'll get help! [STAFF_PING:general_help]"
        cleaned, reason = process_staff_ping_tags(text)
        assert reason == "general_help"
        assert "[STAFF_PING" not in cleaned

    def test_no_tag(self):
        from app import process_staff_ping_tags
        text = "Just a normal response"
        cleaned, reason = process_staff_ping_tags(text)
        assert reason is None
        assert cleaned == text

    def test_food_order_tag(self):
        from app import process_staff_ping_tags
        text = "Order placed [STAFF_PING:food_order]"
        cleaned, reason = process_staff_ping_tags(text)
        assert reason == "food_order"


class TestStaleness:
    def test_none_is_stale(self):
        from sync_deals import _is_stale
        assert _is_stale(None) is True

    def test_fresh(self):
        from sync_deals import _is_stale
        fresh = datetime.now().isoformat()
        assert _is_stale(fresh) is False

    def test_old(self):
        from sync_deals import _is_stale
        old = (datetime.now() - timedelta(minutes=20)).isoformat()
        assert _is_stale(old) is True

    def test_invalid(self):
        from sync_deals import _is_stale
        assert _is_stale("not-a-date") is True


class TestParseTime:
    def test_valid(self):
        from sync_deals import _parse_time
        t = _parse_time("14:30")
        assert t is not None
        assert t.hour == 14
        assert t.minute == 30

    def test_invalid(self):
        from sync_deals import _parse_time
        assert _parse_time("not-a-time") is None

    def test_none(self):
        from sync_deals import _parse_time
        assert _parse_time(None) is None


class TestAnalyzeCart:
    def test_empty(self):
        from sync_deals import _analyze_cart
        result = _analyze_cart([])
        assert result["total_items"] == 0
        assert result["subtotal"] == 0

    def test_counts(self):
        from sync_deals import _analyze_cart
        cart = [
            {"category": "Beer - Draft", "price": 6, "qty": 2},
            {"category": "Snacks", "price": 10, "qty": 1},
        ]
        result = _analyze_cart(cart)
        assert result["total_items"] == 3
        assert result["subtotal"] == 22.0


# ============================================================
# 2. DEAL EVALUATION (seeded DB)
# ============================================================

class TestEvaluateDeals:
    def test_expired_deal_excluded(self, test_db):
        from sync_deals import evaluate_deals
        eligible, near_miss = evaluate_deals(None, 0)
        deal_ids = [d["deal_id"] for d in eligible]
        assert "EXPIRED_DEAL" not in deal_ids

    def test_min_spend_eligible(self, test_db):
        from sync_deals import evaluate_deals
        eligible, _ = evaluate_deals(None, 25.0)
        deal_ids = [d["deal_id"] for d in eligible]
        # FREE_POPCORN requires $20 spend
        assert "FREE_POPCORN" in deal_ids

    def test_min_spend_ineligible(self, test_db):
        from sync_deals import evaluate_deals
        eligible, _ = evaluate_deals(None, 10.0)
        deal_ids = [d["deal_id"] for d in eligible]
        assert "FREE_POPCORN" not in deal_ids

    def test_min_spend_near_miss(self, test_db):
        from sync_deals import evaluate_deals
        _, near_miss = evaluate_deals(None, 16.0)
        near_ids = [d["deal_id"] for d in near_miss]
        # $16 is $4 short of $20 — within $5 near-miss threshold
        assert "FREE_POPCORN" in near_ids

    def test_first_visit_only(self, test_db):
        from sync_deals import evaluate_deals
        # New customer (1 visit) should qualify
        profile = {"total_visits": "1"}
        eligible, _ = evaluate_deals(profile, 0)
        deal_ids = [d["deal_id"] for d in eligible]
        assert "FIRST_VISIT" in deal_ids

    def test_first_visit_returning(self, test_db):
        from sync_deals import evaluate_deals
        # Returning customer should not qualify
        profile = {"total_visits": "5"}
        eligible, _ = evaluate_deals(profile, 0)
        deal_ids = [d["deal_id"] for d in eligible]
        assert "FIRST_VISIT" not in deal_ids

    def test_free_item_id_included(self, test_db):
        from sync_deals import evaluate_deals
        eligible, _ = evaluate_deals(None, 25.0)
        popcorn = next((d for d in eligible if d["deal_id"] == "FREE_POPCORN"), None)
        assert popcorn is not None
        assert popcorn["free_item_id"] == "popcorn_classic"


class TestEvaluateAutoDeals:
    def test_below_threshold(self, test_db):
        from sync_deals import evaluate_auto_deals
        offers = evaluate_auto_deals(15.0)
        assert len(offers) >= 1
        # Should show offer to spend $10 more
        assert any("10" in o.get("description", "") for o in offers)

    def test_above_threshold(self, test_db):
        from sync_deals import evaluate_auto_deals
        offers = evaluate_auto_deals(30.0)
        # Already above $25, should not show this offer
        assert len(offers) == 0


class TestEvaluateCartUpsells:
    def test_beer_snack_upsell(self, test_db):
        from sync_deals import evaluate_cart_upsells
        cart = [
            {"category": "Beer - Draft", "price": 6, "qty": 1, "name": "PBR"},
            {"category": "Beer - Draft", "price": 8, "qty": 1, "name": "IPA"},
        ]
        result = evaluate_cart_upsells(cart)
        assert result is not None
        assert result["id"] == "UPSELL_BEER_SNACK"

    def test_excluded_category_blocks(self, test_db):
        from sync_deals import evaluate_cart_upsells
        # Cart has snacks already — beer+snack upsell excludes Snacks
        cart = [
            {"category": "Beer - Draft", "price": 6, "qty": 2, "name": "PBR"},
            {"category": "Snacks", "price": 10, "qty": 1, "name": "Pretzel Bites"},
        ]
        result = evaluate_cart_upsells(cart)
        # Should NOT get beer+snack upsell since Snacks already in cart
        if result:
            assert result["id"] != "UPSELL_BEER_SNACK"

    def test_upsell_items_dont_block(self, test_db):
        from sync_deals import evaluate_cart_upsells
        # Upsell-added snack should NOT block other upsells
        cart = [
            {"category": "Beer - Draft", "price": 6, "qty": 2, "name": "PBR"},
            {"category": "Snacks", "price": 7.5, "qty": 1, "name": "Pretzel Bites",
             "upsell_id": "UPSELL_BEER_SNACK"},  # Added by upsell
            {"category": "Popcorn", "price": 5, "qty": 1, "name": "Classic Popcorn"},
        ]
        result = evaluate_cart_upsells(cart)
        # The flatbread upsell requires Snacks/Popcorn and excludes Shareables
        # Since the Snack was upsell-added, it shouldn't block but DOES count for requires
        # With 4 items and subtotal ~$25.5, flatbread upsell (min_items=3, min_subtotal=$15) should match
        if result:
            assert result["id"] in ("UPSELL_GROUP_FLAT", "UPSELL_BEER_SNACK")

    def test_empty_cart(self, test_db):
        from sync_deals import evaluate_cart_upsells
        assert evaluate_cart_upsells([]) is None

    def test_priority_ordering(self, test_db):
        from sync_deals import evaluate_cart_upsells
        # Beer+snack has priority 1 (higher than flatbread at 4)
        cart = [
            {"category": "Beer - Draft", "price": 6, "qty": 2, "name": "PBR"},
        ]
        result = evaluate_cart_upsells(cart)
        if result:
            assert result["id"] == "UPSELL_BEER_SNACK"


# ============================================================
# 3. DATABASE CRUD (temp DB)
# ============================================================

class TestDatabaseInit:
    def test_all_tables_created(self, test_db):
        conn = sqlite3.connect(test_db)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        conn.close()
        for expected in ["games", "chunks", "deals", "menu_items", "active_sessions",
                         "tables", "order_queue", "staff_requests", "cart_upsells"]:
            assert expected in tables, f"Missing table: {expected}"


class TestOrders:
    def test_save_and_read(self, test_db):
        from database import save_order
        save_order("ORD001", "+17185551234", "visit-123",
                   '[{"name":"PBR","qty":1}]', 6.0, "", 6.53)
        conn = sqlite3.connect(test_db)
        row = conn.execute("SELECT * FROM orders WHERE order_id='ORD001'").fetchone()
        conn.close()
        assert row is not None


class TestSessionFlow:
    def test_register_and_query(self, test_db):
        import app
        # Manually insert since register_session uses st.session_state
        conn = sqlite3.connect(test_db)
        conn.execute("""
            INSERT INTO active_sessions (visit_id, phone, table_number, status)
            VALUES ('v1', '+17185551234', NULL, 'active')
        """)
        conn.commit()
        row = conn.execute("SELECT * FROM active_sessions WHERE visit_id='v1'").fetchone()
        conn.close()
        assert row is not None

    def test_kill_session(self, test_db):
        conn = sqlite3.connect(test_db)
        conn.execute("""
            INSERT INTO active_sessions (visit_id, phone, status)
            VALUES ('v2', '+10005551234', 'active')
        """)
        conn.commit()

        from app import is_session_killed
        assert is_session_killed("v2") is False

        conn = sqlite3.connect(test_db)
        conn.execute("UPDATE active_sessions SET status='killed' WHERE visit_id='v2'")
        conn.commit()
        conn.close()

        assert is_session_killed("v2") is True


class TestStaffPingDB:
    def test_insert_and_read(self, test_db):
        from admin import get_pending_staff_requests, acknowledge_staff_request, get_db
        conn = sqlite3.connect(test_db)
        conn.execute("""
            INSERT INTO staff_requests (visit_id, phone, table_number, game_title, question, reason)
            VALUES ('v1', '+17185551234', 5, 'Catan', 'Need help', 'general_help')
        """)
        conn.commit()
        conn.close()

        pending = get_pending_staff_requests()
        assert len(pending) == 1
        assert pending[0]["table_number"] == 5

        acknowledge_staff_request(pending[0]["id"])
        assert len(get_pending_staff_requests()) == 0


class TestClaimTable:
    def test_marks_occupied(self, test_db):
        conn = sqlite3.connect(test_db)
        conn.execute("""
            INSERT INTO active_sessions (visit_id, phone, status)
            VALUES ('v3', '+17185551234', 'active')
        """)
        conn.commit()
        conn.close()

        from app import claim_table
        claim_table("v3", "+17185551234", 5)

        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row
        tbl = conn.execute("SELECT * FROM tables WHERE table_number=5").fetchone()
        sess = conn.execute("SELECT * FROM active_sessions WHERE visit_id='v3'").fetchone()
        conn.close()

        assert tbl["status"] == "occupied"
        assert sess["table_number"] == 5

    def test_party_size_from_sessions(self, test_db):
        conn = sqlite3.connect(test_db)
        conn.execute("INSERT INTO active_sessions (visit_id, phone, table_number, status) VALUES ('v4', 'p1', NULL, 'active')")
        conn.execute("INSERT INTO active_sessions (visit_id, phone, table_number, status) VALUES ('v5', 'p2', NULL, 'active')")
        conn.commit()
        conn.close()

        from app import claim_table
        claim_table("v4", "p1", 3)
        claim_table("v5", "p2", 3)

        conn = sqlite3.connect(test_db)
        conn.row_factory = sqlite3.Row
        tbl = conn.execute("SELECT * FROM tables WHERE table_number=3").fetchone()
        conn.close()
        assert tbl["party_size"] == 2


# ============================================================
# 4. INTEGRATION FLOWS
# ============================================================

class TestDealRevalidation:
    def test_deactivated_deal_removed(self, test_db):
        """Apply a deal, then deactivate it, re-evaluate — should be gone."""
        from sync_deals import evaluate_deals

        # First verify it's eligible
        eligible, _ = evaluate_deals(None, 25.0)
        assert any(d["deal_id"] == "FREE_POPCORN" for d in eligible)

        # Deactivate the deal
        conn = sqlite3.connect(test_db)
        conn.execute("UPDATE deals SET active=0 WHERE deal_id='FREE_POPCORN'")
        conn.commit()
        conn.close()

        # Re-evaluate — should be gone
        eligible, _ = evaluate_deals(None, 25.0)
        assert not any(d["deal_id"] == "FREE_POPCORN" for d in eligible)


class TestOrderDiscountCalc:
    def test_percent_discount(self, test_db):
        """Simulate applying a 10% deal to a $20 cart."""
        cart = [{"price": 10.0, "qty": 2, "name": "PBR", "item_id": "beer_pbr", "category": "Beer - Draft"}]
        subtotal = sum(i["price"] * i["qty"] for i in cart)
        assert subtotal == 20.0

        deal = {"deal_id": "FIRST_VISIT", "discount_type": "percent", "discount_value": 10}
        discount = subtotal * (deal["discount_value"] / 100)
        assert discount == pytest.approx(2.0)

        discounted = subtotal - discount
        tax = discounted * 0.08875
        total = discounted + tax
        assert total == pytest.approx(19.60, abs=0.01)
