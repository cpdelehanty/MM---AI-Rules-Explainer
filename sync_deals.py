"""
Deals, events, and auto-deal rules sync from Google Sheets to SQLite cache.
Also handles deal eligibility evaluation and prompt formatting.
"""

import sqlite3
import json
import os
from datetime import datetime, date, timedelta

from database import (
    DB_PATH, init_database,
    get_active_deals, get_last_deals_sync,
    get_active_events, get_last_events_sync,
    get_auto_deal_rules, get_last_auto_rules_sync,
    log_security_event,
)


# --- Sync checks ---

def should_sync_deals():
    """Check if we need to sync deals today"""
    return get_last_deals_sync() is None


def should_sync_events():
    """Check if we need to sync events today"""
    return get_last_events_sync() is None


def should_sync_auto_rules():
    """Check if we need to sync auto-deal rules today"""
    return get_last_auto_rules_sync() is None


# --- Google Sheets auth helper ---

def _get_sheets_client():
    """Authenticate and return gspread client + sheet ID. Returns (client, sheet_id) or (None, None)."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        return None, None

    sheet_id = os.environ.get("GOOGLE_SHEETS_MENU_ID")
    service_account_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

    if not sheet_id or not service_account_json:
        return None, None

    creds_dict = json.loads(service_account_json)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)

    return client, sheet_id


# --- Deals sync ---

def sync_deals_from_sheets():
    """Pull deals from Google Sheet and upsert into SQLite."""
    client, sheet_id = _get_sheets_client()
    if not client:
        return {"success": False, "deals_synced": 0, "message": "Missing credentials or gspread not installed"}

    try:
        sheet = client.open_by_key(sheet_id)
        worksheet = sheet.worksheet("Deals")
        records = worksheet.get_all_records()

        if not records:
            return {"success": False, "deals_synced": 0, "message": "No records found in Deals worksheet"}

        conn = sqlite3.connect(DB_PATH, timeout=10)
        cursor = conn.cursor()

        seen_ids = set()
        synced_count = 0

        for row in records:
            deal_id = str(row.get("deal_id", "")).strip()
            if not deal_id:
                continue

            seen_ids.add(deal_id)
            active = 1 if str(row.get("active", "1")).strip().lower() in ("yes", "1", "true") else 0
            first_visit = 1 if str(row.get("first_visit_only", "0")).strip().lower() in ("yes", "1", "true") else 0

            def safe_float(val, default=0):
                try:
                    return float(val) if val != "" else default
                except (ValueError, TypeError):
                    return default

            def safe_int(val, default=0):
                try:
                    return int(val) if val != "" else default
                except (ValueError, TypeError):
                    return default

            cursor.execute("""
                INSERT INTO deals (deal_id, name, display_text, discount_type, discount_value,
                    free_item_description, target_category, min_spend, min_visit_count, min_party_size,
                    first_visit_only, time_of_day_start, time_of_day_end, days_of_week,
                    active, expiry_date, last_synced)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(deal_id) DO UPDATE SET
                    name = excluded.name,
                    display_text = excluded.display_text,
                    discount_type = excluded.discount_type,
                    discount_value = excluded.discount_value,
                    free_item_description = excluded.free_item_description,
                    target_category = excluded.target_category,
                    min_spend = excluded.min_spend,
                    min_visit_count = excluded.min_visit_count,
                    min_party_size = excluded.min_party_size,
                    first_visit_only = excluded.first_visit_only,
                    time_of_day_start = excluded.time_of_day_start,
                    time_of_day_end = excluded.time_of_day_end,
                    days_of_week = excluded.days_of_week,
                    active = excluded.active,
                    expiry_date = excluded.expiry_date,
                    last_synced = CURRENT_TIMESTAMP
            """, (
                deal_id,
                str(row.get("name", "")).strip(),
                str(row.get("display_text", "")).strip(),
                str(row.get("discount_type", "percent")).strip(),
                safe_float(row.get("discount_value")),
                str(row.get("free_item_description", "")).strip(),
                str(row.get("target_category", "")).strip() or None,
                safe_float(row.get("min_spend")),
                safe_int(row.get("min_visit_count")),
                safe_int(row.get("min_party_size", 1), default=1),
                first_visit,
                str(row.get("time_of_day_start", "")).strip() or None,
                str(row.get("time_of_day_end", "")).strip() or None,
                str(row.get("days_of_week", "")).strip() or None,
                active,
                str(row.get("expiry_date", "")).strip() or None,
            ))
            synced_count += 1

        # Soft-delete: deals removed from sheet get active=0
        if seen_ids:
            placeholders = ",".join("?" * len(seen_ids))
            cursor.execute(f"UPDATE deals SET active = 0 WHERE deal_id NOT IN ({placeholders})", list(seen_ids))

        cursor.execute("INSERT INTO deals_sync_log (deals_synced, status) VALUES (?, 'success')", (synced_count,))
        conn.commit()
        conn.close()

        return {"success": True, "deals_synced": synced_count, "message": f"Synced {synced_count} deals"}

    except Exception as e:
        print(f"[DEALS SYNC] Error: {e}")
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            conn.execute("INSERT INTO deals_sync_log (deals_synced, status) VALUES (0, ?)", (f"error: {str(e)[:200]}",))
            conn.commit()
            conn.close()
        except Exception:
            pass
        return {"success": False, "deals_synced": 0, "message": str(e)}


# --- Events sync ---

def sync_events_from_sheets():
    """Pull events from Google Sheet and upsert into SQLite."""
    client, sheet_id = _get_sheets_client()
    if not client:
        return {"success": False, "events_synced": 0, "message": "Missing credentials or gspread not installed"}

    try:
        sheet = client.open_by_key(sheet_id)
        worksheet = sheet.worksheet("Events")
        records = worksheet.get_all_records()

        if not records:
            return {"success": False, "events_synced": 0, "message": "No records found in Events worksheet"}

        conn = sqlite3.connect(DB_PATH, timeout=10)
        cursor = conn.cursor()

        seen_ids = set()
        synced_count = 0

        for row in records:
            event_id = str(row.get("event_id", "")).strip()
            if not event_id:
                continue

            seen_ids.add(event_id)
            active = 1 if str(row.get("active", "1")).strip().lower() in ("yes", "1", "true") else 0

            cursor.execute("""
                INSERT INTO events (event_id, name, description, date, time, game, display_text, active, last_synced)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(event_id) DO UPDATE SET
                    name = excluded.name,
                    description = excluded.description,
                    date = excluded.date,
                    time = excluded.time,
                    game = excluded.game,
                    display_text = excluded.display_text,
                    active = excluded.active,
                    last_synced = CURRENT_TIMESTAMP
            """, (
                event_id,
                str(row.get("name", "")).strip(),
                str(row.get("description", "")).strip(),
                str(row.get("date", "")).strip() or None,
                str(row.get("time", "")).strip() or None,
                str(row.get("game", "")).strip() or None,
                str(row.get("display_text", "")).strip(),
                active,
            ))
            synced_count += 1

        if seen_ids:
            placeholders = ",".join("?" * len(seen_ids))
            cursor.execute(f"UPDATE events SET active = 0 WHERE event_id NOT IN ({placeholders})", list(seen_ids))

        cursor.execute("INSERT INTO events_sync_log (events_synced, status) VALUES (?, 'success')", (synced_count,))
        conn.commit()
        conn.close()

        return {"success": True, "events_synced": synced_count, "message": f"Synced {synced_count} events"}

    except Exception as e:
        print(f"[EVENTS SYNC] Error: {e}")
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            conn.execute("INSERT INTO events_sync_log (events_synced, status) VALUES (0, ?)", (f"error: {str(e)[:200]}",))
            conn.commit()
            conn.close()
        except Exception:
            pass
        return {"success": False, "events_synced": 0, "message": str(e)}


# --- Auto-deal rules sync ---

def sync_auto_rules_from_sheets():
    """Pull auto-deal rules from Google Sheet and upsert into SQLite."""
    client, sheet_id = _get_sheets_client()
    if not client:
        return {"success": False, "rules_synced": 0, "message": "Missing credentials"}

    try:
        sheet = client.open_by_key(sheet_id)
        try:
            worksheet = sheet.worksheet("Auto Rules")
        except Exception:
            # Tab doesn't exist yet — not an error
            return {"success": True, "rules_synced": 0, "message": "No Auto Rules tab found (optional)"}

        records = worksheet.get_all_records()
        if not records:
            return {"success": True, "rules_synced": 0, "message": "No auto rules defined"}

        conn = sqlite3.connect(DB_PATH, timeout=10)
        cursor = conn.cursor()

        seen_ids = set()
        synced_count = 0

        for row in records:
            rule_id = str(row.get("rule_id", "")).strip()
            if not rule_id:
                continue

            seen_ids.add(rule_id)
            active = 1 if str(row.get("active", "1")).strip().lower() in ("yes", "1", "true") else 0

            def safe_float(val, default=0):
                try:
                    return float(val) if val != "" else default
                except (ValueError, TypeError):
                    return default

            cursor.execute("""
                INSERT INTO auto_deal_rules (rule_id, name, min_spend_threshold, discount_percent,
                    max_discount, display_template, active, last_synced)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(rule_id) DO UPDATE SET
                    name = excluded.name,
                    min_spend_threshold = excluded.min_spend_threshold,
                    discount_percent = excluded.discount_percent,
                    max_discount = excluded.max_discount,
                    display_template = excluded.display_template,
                    active = excluded.active,
                    last_synced = CURRENT_TIMESTAMP
            """, (
                rule_id,
                str(row.get("name", "")).strip(),
                safe_float(row.get("min_spend_threshold", 0)),
                safe_float(row.get("discount_percent", 0)),
                safe_float(row.get("max_discount")) or None,
                str(row.get("display_template", "")).strip(),
                active,
            ))
            synced_count += 1

        if seen_ids:
            placeholders = ",".join("?" * len(seen_ids))
            cursor.execute(f"UPDATE auto_deal_rules SET active = 0 WHERE rule_id NOT IN ({placeholders})", list(seen_ids))

        cursor.execute("INSERT INTO auto_rules_sync_log (rules_synced, status) VALUES (?, 'success')", (synced_count,))
        conn.commit()
        conn.close()

        return {"success": True, "rules_synced": synced_count, "message": f"Synced {synced_count} auto-deal rules"}

    except Exception as e:
        print(f"[AUTO RULES SYNC] Error: {e}")
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            conn.execute("INSERT INTO auto_rules_sync_log (rules_synced, status) VALUES (0, ?)", (f"error: {str(e)[:200]}",))
            conn.commit()
            conn.close()
        except Exception:
            pass
        return {"success": False, "rules_synced": 0, "message": str(e)}


# --- Cart Upsells Sync ---

def should_sync_cart_upsells():
    """Check if we need to sync cart upsells today."""
    from database import get_last_cart_upsells_sync
    return get_last_cart_upsells_sync() is None


def sync_cart_upsells_from_sheets():
    """Pull cart upsell rules from Google Sheet and upsert into SQLite."""
    client, sheet_id = _get_sheets_client()
    if not client:
        return {"success": False, "rules_synced": 0, "message": "Missing credentials"}

    try:
        sheet = client.open_by_key(sheet_id)
        try:
            worksheet = sheet.worksheet("Cart Upsells")
        except Exception:
            return {"success": True, "rules_synced": 0, "message": "No Cart Upsells tab found (optional)"}

        records = worksheet.get_all_records()
        if not records:
            return {"success": True, "rules_synced": 0, "message": "No cart upsell rules defined"}

        conn = sqlite3.connect(DB_PATH, timeout=10)
        cursor = conn.cursor()

        seen_ids = set()
        synced_count = 0

        for row in records:
            upsell_id = str(row.get("upsell_id", "")).strip()
            if not upsell_id:
                continue

            seen_ids.add(upsell_id)
            active = 1 if str(row.get("active", "1")).strip().lower() in ("yes", "1", "true") else 0

            def safe_int(val, default=0):
                try:
                    return int(val) if val != "" else default
                except (ValueError, TypeError):
                    return default

            def safe_float(val, default=0):
                try:
                    return float(val) if val != "" else default
                except (ValueError, TypeError):
                    return default

            cursor.execute("""
                INSERT INTO cart_upsells (upsell_id, name, requires_categories, excludes_categories,
                    min_requires_count, min_items, max_items, min_subtotal, max_subtotal,
                    target_category, discount_percent, message, suggested_items, priority, active, last_synced)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(upsell_id) DO UPDATE SET
                    name = excluded.name,
                    requires_categories = excluded.requires_categories,
                    excludes_categories = excluded.excludes_categories,
                    min_requires_count = excluded.min_requires_count,
                    min_items = excluded.min_items,
                    max_items = excluded.max_items,
                    min_subtotal = excluded.min_subtotal,
                    max_subtotal = excluded.max_subtotal,
                    target_category = excluded.target_category,
                    discount_percent = excluded.discount_percent,
                    message = excluded.message,
                    suggested_items = excluded.suggested_items,
                    priority = excluded.priority,
                    active = excluded.active,
                    last_synced = CURRENT_TIMESTAMP
            """, (
                upsell_id,
                str(row.get("name", "")).strip(),
                str(row.get("requires_categories", "")).strip() or None,
                str(row.get("excludes_categories", "")).strip() or None,
                safe_int(row.get("min_requires_count", 1), default=1),
                safe_int(row.get("min_items", 0)),
                safe_int(row.get("max_items", 0)),
                safe_float(row.get("min_subtotal", 0)),
                safe_float(row.get("max_subtotal", 0)),
                str(row.get("target_category", "")).strip(),
                safe_float(row.get("discount_percent", 0)),
                str(row.get("message", "")).strip(),
                str(row.get("suggested_items", "")).strip() or None,
                safe_int(row.get("priority", 10), default=10),
                active,
            ))
            synced_count += 1

        if seen_ids:
            placeholders = ",".join("?" * len(seen_ids))
            cursor.execute(f"UPDATE cart_upsells SET active = 0 WHERE upsell_id NOT IN ({placeholders})", list(seen_ids))

        cursor.execute("INSERT INTO cart_upsells_sync_log (rules_synced, status) VALUES (?, 'success')", (synced_count,))
        conn.commit()
        conn.close()

        return {"success": True, "rules_synced": synced_count, "message": f"Synced {synced_count} cart upsell rules"}

    except Exception as e:
        print(f"[CART UPSELLS SYNC] Error: {e}")
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            conn.execute("INSERT INTO cart_upsells_sync_log (rules_synced, status) VALUES (0, ?)", (f"error: {str(e)[:200]}",))
            conn.commit()
            conn.close()
        except Exception:
            pass
        return {"success": False, "rules_synced": 0, "message": str(e)}


# --- Order transmission ---

def transmit_order_to_sheet(order_id, phone, items_json, deals_json, subtotal, total):
    """Write a placed order to the Orders tab in Google Sheets."""
    client, sheet_id = _get_sheets_client()
    if not client:
        print("[ORDER] Cannot transmit — missing credentials")
        return False

    try:
        sheet = client.open_by_key(sheet_id)
        try:
            worksheet = sheet.worksheet("Orders")
        except Exception:
            # Create Orders tab if it doesn't exist
            worksheet = sheet.add_worksheet(title="Orders", rows=1000, cols=8)
            worksheet.append_row(["order_id", "phone", "timestamp", "items", "deals_applied", "subtotal", "total", "status"])

        worksheet.append_row([
            order_id,
            phone or "ANON",
            datetime.now().isoformat(),
            items_json,
            deals_json or "",
            subtotal,
            total,
            "pending",
        ])
        return True

    except Exception as e:
        print(f"[ORDER] Error transmitting to sheet: {e}")
        return False


# --- Deal eligibility evaluation ---

def _parse_time(time_str):
    """Parse HH:MM time string to datetime.time, returns None on failure."""
    try:
        parts = time_str.strip().split(":")
        return datetime.now().replace(hour=int(parts[0]), minute=int(parts[1]), second=0, microsecond=0).time()
    except (ValueError, IndexError, AttributeError):
        return None


def _time_diff_minutes(t1, t2):
    """Get difference in minutes between two time objects."""
    d1 = datetime.combine(date.today(), t1)
    d2 = datetime.combine(date.today(), t2)
    diff = (d2 - d1).total_seconds() / 60
    return diff


def evaluate_deals(customer_profile=None, cart_subtotal=0):
    """
    Evaluate all active deals against customer state.

    Returns (eligible_deals, near_miss_deals) — each a list of dicts.
    """
    deals = get_active_deals()
    now = datetime.now()
    today = date.today()
    current_time = now.time()
    current_day = now.strftime("%a")  # Mon, Tue, etc.

    eligible = []
    near_miss = []

    visits = 0
    if customer_profile:
        try:
            visits = int(customer_profile.get("total_visits", 0))
        except (ValueError, TypeError):
            visits = 0

    for deal in deals:
        # Skip expired deals
        if deal.get("expiry_date"):
            try:
                exp = datetime.strptime(deal["expiry_date"], "%Y-%m-%d").date()
                if exp < today:
                    continue
            except ValueError:
                pass

        # Skip deals requiring party size (can't check yet)
        if deal.get("min_party_size", 1) > 1:
            continue

        # Track failures for near-miss classification
        failures = []

        # First visit only
        if deal.get("first_visit_only"):
            if customer_profile is None:
                continue  # Anonymous — can't verify
            if visits > 1:
                failures.append({"type": "first_visit", "gap": "first-visit customers only"})

        # Min visit count
        min_visits = deal.get("min_visit_count", 0)
        if min_visits > 0:
            if customer_profile is None:
                continue  # Anonymous — can't verify
            if visits < min_visits:
                diff = min_visits - visits
                if diff <= 1:
                    failures.append({"type": "visit_count", "gap": f"visit {diff} more time{'s' if diff > 1 else ''}"})
                else:
                    continue  # Too far off — don't show

        # Min spend
        min_spend = deal.get("min_spend", 0)
        if min_spend > 0:
            if cart_subtotal < min_spend:
                diff = min_spend - cart_subtotal
                if diff <= 5.0:
                    failures.append({"type": "spend", "gap": f"spend ${diff:.2f} more"})
                else:
                    continue  # Too far off

        # Time of day
        start_str = deal.get("time_of_day_start")
        end_str = deal.get("time_of_day_end")
        if start_str and end_str:
            start_time = _parse_time(start_str)
            end_time = _parse_time(end_str)
            if start_time and end_time:
                # Check if currently in window
                if start_time <= end_time:
                    in_window = start_time <= current_time <= end_time
                else:
                    # Overnight (e.g., 22:00 - 02:00)
                    in_window = current_time >= start_time or current_time <= end_time

                if not in_window:
                    # Check near-miss: within 15 minutes of window opening
                    mins_to_start = _time_diff_minutes(current_time, start_time)
                    if 0 < mins_to_start <= 15:
                        failures.append({
                            "type": "time",
                            "gap": f"starts in {int(mins_to_start)} minutes (at {start_str})"
                        })
                    else:
                        continue  # Outside window and not near

        # Days of week
        days_str = deal.get("days_of_week")
        if days_str:
            allowed_days = [d.strip().lower()[:3] for d in days_str.split(",")]
            if current_day.lower() not in allowed_days:
                continue  # Wrong day — no near-miss for this

        # Classify
        if len(failures) == 0:
            eligible.append({
                "deal_id": deal["deal_id"],
                "display_text": deal["display_text"],
                "discount_type": deal.get("discount_type", ""),
                "discount_value": deal.get("discount_value", 0),
                "free_item_description": deal.get("free_item_description", ""),
                "target_category": deal.get("target_category", ""),
            })
        elif len(failures) == 1:
            near_miss.append({
                "deal_id": deal["deal_id"],
                "display_text": deal["display_text"],
                "discount_type": deal.get("discount_type", ""),
                "discount_value": deal.get("discount_value", 0),
                "free_item_description": deal.get("free_item_description", ""),
                "target_category": deal.get("target_category", ""),
                "gap": failures[0]["gap"],
            })
        # 2+ failures → not shown

    return eligible, near_miss


def evaluate_auto_deals(cart_subtotal=0):
    """
    Evaluate auto-deal rules and generate dynamic spend-threshold offers.

    Returns list of auto offers with gap text.
    """
    rules = get_auto_deal_rules()
    offers = []

    for rule in rules:
        threshold = rule.get("min_spend_threshold", 0)
        if cart_subtotal < threshold:
            gap = threshold - cart_subtotal
            template = rule.get("display_template", "")
            # Replace template placeholders
            offer_text = template.replace("{gap}", f"${gap:.2f}").replace("{threshold}", f"${threshold:.2f}")
            discount_pct = rule.get("discount_percent", 0)
            offer_text = offer_text.replace("{discount}", f"{discount_pct:.0f}%")
            offers.append({
                "rule_id": rule["rule_id"],
                "description": offer_text,
            })

    return offers


# --- Cart-Based Upsell Engine ---

# Category groupings for cart analysis
DRINK_CATEGORIES = {"beer - draft", "beer - canned", "wine", "non-alc"}
BEER_CATEGORIES = {"beer - draft", "beer - canned"}
WINE_CATEGORIES = {"wine"}
NON_ALC_CATEGORIES = {"non-alc"}
FOOD_CATEGORIES = {"popcorn", "snacks", "shareables", "sweets"}
SNACK_CATEGORIES = {"popcorn", "snacks"}
SWEET_CATEGORIES = {"sweets"}
SHAREABLE_CATEGORIES = {"shareables"}


def _analyze_cart(cart):
    """Analyze cart composition by category counts."""
    analysis = {
        "total_items": 0,
        "subtotal": 0,
        "drinks": 0,
        "beers": 0,
        "wines": 0,
        "non_alc": 0,
        "food": 0,
        "snacks": 0,
        "sweets": 0,
        "shareables": 0,
        "popcorn": 0,
        "categories_present": set(),
    }

    for item in cart:
        cat = item.get("category", "").lower()
        price = float(item.get("price", 0) or 0)
        qty = int(item.get("quantity", 1) or 1)

        analysis["total_items"] += qty
        analysis["subtotal"] += price * qty
        analysis["categories_present"].add(cat)

        if cat in DRINK_CATEGORIES:
            analysis["drinks"] += qty
        if cat in BEER_CATEGORIES:
            analysis["beers"] += qty
        if cat in WINE_CATEGORIES:
            analysis["wines"] += qty
        if cat in NON_ALC_CATEGORIES:
            analysis["non_alc"] += qty
        if cat in FOOD_CATEGORIES:
            analysis["food"] += qty
        if cat in SNACK_CATEGORIES:
            analysis["snacks"] += qty
        if cat == "sweets":
            analysis["sweets"] += qty
        if cat == "shareables":
            analysis["shareables"] += qty
        if cat == "popcorn":
            analysis["popcorn"] += qty

    return analysis


def evaluate_cart_upsells(cart):
    """
    Evaluate cart composition against sheet-driven upsell rules.
    Returns at most ONE upsell (the highest-priority match, lowest number = highest priority).

    Rules are loaded from the cart_upsells DB table (synced from Google Sheets).
    Each rule specifies:
    - requires_categories: comma-separated categories that must be in the cart
    - excludes_categories: comma-separated categories that must NOT be in the cart
    - min_requires_count: minimum number of items from required categories
    - min_items / max_items: total cart item count constraints (0 = no limit)
    - min_subtotal / max_subtotal: cart subtotal constraints (0 = no limit)
    """
    if not cart:
        return None

    a = _analyze_cart(cart)
    from database import get_cart_upsell_rules
    rules = get_cart_upsell_rules()  # already ordered by priority ASC

    for rule in rules:
        # Parse category lists
        requires_raw = rule.get("requires_categories", "") or ""
        excludes_raw = rule.get("excludes_categories", "") or ""
        requires_cats = {c.strip().lower() for c in requires_raw.split(",") if c.strip()}
        excludes_cats = {c.strip().lower() for c in excludes_raw.split(",") if c.strip()}

        min_req_count = int(rule.get("min_requires_count", 1) or 1)
        min_items = int(rule.get("min_items", 0) or 0)
        max_items = int(rule.get("max_items", 0) or 0)
        min_subtotal = float(rule.get("min_subtotal", 0) or 0)
        max_subtotal = float(rule.get("max_subtotal", 0) or 0)

        # Check requires: count items in required categories
        if requires_cats:
            req_count = sum(
                int(item.get("quantity", 1) or 1)
                for item in cart
                if item.get("category", "").lower() in requires_cats
            )
            if req_count < min_req_count:
                continue

        # Check excludes: none of these categories should be in cart
        if excludes_cats:
            has_excluded = any(
                item.get("category", "").lower() in excludes_cats
                for item in cart
            )
            if has_excluded:
                continue

        # Check item count constraints
        if min_items > 0 and a["total_items"] < min_items:
            continue
        if max_items > 0 and a["total_items"] > max_items:
            continue

        # Check subtotal constraints
        if min_subtotal > 0 and a["subtotal"] < min_subtotal:
            continue
        if max_subtotal > 0 and a["subtotal"] > max_subtotal:
            continue

        # All conditions met — build the result
        suggested_raw = rule.get("suggested_items", "") or ""
        suggested_list = [s.strip() for s in suggested_raw.split(",") if s.strip()]

        return {
            "id": rule["upsell_id"],
            "target_category": rule.get("target_category", ""),
            "discount_percent": float(rule.get("discount_percent", 0) or 0),
            "message": rule.get("message", ""),
            "suggested_items": suggested_list,
        }

    return None


# --- Events ---

def get_relevant_events(game_title=None):
    """Get upcoming events (next 30 days), optionally filtered by game."""
    events = get_active_events()
    today = date.today()
    cutoff = today + timedelta(days=30)
    relevant = []

    for event in events:
        event_date_str = event.get("date", "")
        if not event_date_str:
            continue

        try:
            event_date = datetime.strptime(event_date_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        if event_date < today or event_date > cutoff:
            continue

        # If game_title specified, only include events for that game (or unscoped events)
        event_game = (event.get("game") or "").strip()
        if game_title and event_game and event_game.lower() != game_title.lower():
            continue

        relevant.append({
            "event_id": event["event_id"],
            "name": event["name"],
            "date": event_date_str,
            "time": event.get("time", ""),
            "game": event_game,
            "display_text": event["display_text"],
        })

    return relevant


# --- Prompt formatting ---

def format_deals_for_prompt(customer_profile=None, cart_subtotal=0):
    """Build structured deals context block for Claude prompt."""
    eligible, near_miss = evaluate_deals(customer_profile, cart_subtotal)
    auto_offers = evaluate_auto_deals(cart_subtotal)

    if not eligible and not near_miss and not auto_offers:
        return ""

    lines = []
    if eligible:
        lines.append(f"ELIGIBLE_DEALS: {json.dumps(eligible)}")
    if near_miss:
        lines.append(f"NEAR_MISS_DEALS: {json.dumps(near_miss)}")
    if auto_offers:
        lines.append(f"AUTO_OFFERS: {json.dumps(auto_offers)}")
    return "\n".join(lines)


def format_events_for_prompt(game_title=None):
    """Build structured events context block for Claude prompt."""
    events = get_relevant_events(game_title)
    if not events:
        return ""
    return f"UPCOMING_EVENTS: {json.dumps(events)}"


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    init_database()

    print("Syncing deals from Google Sheets...")
    result = sync_deals_from_sheets()
    print(f"Deals: {result}")

    print("\nSyncing events from Google Sheets...")
    result = sync_events_from_sheets()
    print(f"Events: {result}")

    print("\nSyncing auto-deal rules from Google Sheets...")
    result = sync_auto_rules_from_sheets()
    print(f"Auto rules: {result}")

    print("\nEvaluating deals (no customer, no cart):")
    eligible, near_miss = evaluate_deals()
    print(f"  Eligible: {eligible}")
    print(f"  Near-miss: {near_miss}")

    print("\nAuto offers (no cart):")
    print(f"  {evaluate_auto_deals()}")

    print("\nUpcoming events:")
    print(f"  {get_relevant_events()}")

    print("\nFormatted deals for prompt:")
    print(format_deals_for_prompt())

    print("\nFormatted events for prompt:")
    print(format_events_for_prompt())
