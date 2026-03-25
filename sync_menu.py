"""
Menu sync from Google Sheets to SQLite cache.
Pulls menu items maintained by owner, caches locally for the AI assistant.
"""

import sqlite3
import json
import os
from datetime import datetime

from database import DB_PATH, init_database, get_menu_items, get_last_menu_sync


def should_sync():
    """Check if we need to sync today (no successful sync yet today)"""
    return get_last_menu_sync() is None


def sync_menu_from_sheets():
    """
    Pull menu data from Google Sheet and upsert into SQLite.
    Returns dict with sync results.
    """
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        return {"success": False, "items_synced": 0, "message": "gspread not installed"}

    # Get credentials
    sheet_id = os.environ.get("GOOGLE_SHEETS_MENU_ID")
    service_account_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

    if not sheet_id or not service_account_json:
        return {"success": False, "items_synced": 0, "message": "Missing GOOGLE_SHEETS_MENU_ID or GOOGLE_SERVICE_ACCOUNT_JSON"}

    try:
        # Auth with service account
        creds_dict = json.loads(service_account_json)
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
        ]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)

        # Open sheet and read all rows
        sheet = client.open_by_key(sheet_id)
        worksheet = sheet.worksheet("Menu")
        records = worksheet.get_all_records()

        if not records:
            return {"success": False, "items_synced": 0, "message": "No records found in Menu worksheet"}

        # Upsert into SQLite
        conn = sqlite3.connect(DB_PATH, timeout=10)
        cursor = conn.cursor()

        # Track which item_ids we see in the sheet
        seen_ids = set()
        synced_count = 0

        for row in records:
            item_id = str(row.get("item_id", "")).strip()
            if not item_id:
                continue

            seen_ids.add(item_id)
            available = 1 if str(row.get("available", "yes")).strip().lower() in ("yes", "1", "true") else 0

            cursor.execute("""
                INSERT INTO menu_items (item_id, category, name, description, price, dietary_tags, available, notes, last_synced)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(item_id) DO UPDATE SET
                    category = excluded.category,
                    name = excluded.name,
                    description = excluded.description,
                    price = excluded.price,
                    dietary_tags = excluded.dietary_tags,
                    available = excluded.available,
                    notes = excluded.notes,
                    last_synced = CURRENT_TIMESTAMP
            """, (
                item_id,
                str(row.get("category", "")).strip(),
                str(row.get("name", "")).strip(),
                str(row.get("description", "")).strip(),
                str(row.get("price", "")).strip(),
                str(row.get("dietary_tags", "")).strip(),
                available,
                str(row.get("notes", "")).strip(),
            ))
            synced_count += 1

        # Mark items not in sheet as unavailable (soft-delete)
        if seen_ids:
            placeholders = ",".join("?" * len(seen_ids))
            cursor.execute(f"""
                UPDATE menu_items SET available = 0
                WHERE item_id NOT IN ({placeholders})
            """, list(seen_ids))

        # Log the sync
        cursor.execute("""
            INSERT INTO menu_sync_log (items_synced, status)
            VALUES (?, 'success')
        """, (synced_count,))

        conn.commit()
        conn.close()

        return {"success": True, "items_synced": synced_count, "message": f"Synced {synced_count} menu items"}

    except Exception as e:
        # Log failure but don't crash — app will use cached data
        print(f"[MENU SYNC] Error: {e}")

        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            conn.execute("""
                INSERT INTO menu_sync_log (items_synced, status)
                VALUES (0, ?)
            """, (f"error: {str(e)[:200]}",))
            conn.commit()
            conn.close()
        except Exception:
            pass

        return {"success": False, "items_synced": 0, "message": str(e)}


def format_menu_for_prompt():
    """
    Build plain-text menu block for injection into Claude's prompt.
    Groups items by category. Only includes available items.
    """
    items = get_menu_items(available_only=True)

    if not items:
        return "[No menu available — staff can help with food & drink options]"

    # Group by category
    categories = {}
    for item in items:
        cat = item["category"] or "Other"
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(item)

    lines = ["=== THE MERRY MEEPLE MENU ===", ""]
    for category, cat_items in categories.items():
        lines.append(f"{category.upper()}:")
        for item in cat_items:
            parts = [f"- {item['name']}"]
            if item.get("price"):
                parts.append(f"— {item['price']}")
            if item.get("description"):
                parts.append(f"— {item['description']}")
            if item.get("dietary_tags"):
                parts.append(f"({item['dietary_tags']})")
            if item.get("notes"):
                parts.append(f"[{item['notes']}]")
            lines.append(" ".join(parts))
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    init_database()

    print("Syncing menu from Google Sheets...")
    result = sync_menu_from_sheets()
    print(f"Result: {result}")

    if result["success"]:
        print("\nFormatted menu for prompt:")
        print(format_menu_for_prompt())
