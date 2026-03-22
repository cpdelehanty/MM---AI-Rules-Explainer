"""
User data store backed by Google Sheets.
Handles customer profiles, visit tracking, and preference storage.
"""

import json
import os
import re
import uuid
from datetime import datetime


def _get_sheets_client():
    """Authenticate and return (gspread_client, spreadsheet) or (None, None)"""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        return None, None

    sheet_id = os.environ.get("GOOGLE_SHEETS_CUSTOMER_ID")
    service_account_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

    if not sheet_id or not service_account_json:
        return None, None

    try:
        creds_dict = json.loads(service_account_json)
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
        ]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(sheet_id)
        return client, spreadsheet
    except Exception as e:
        print(f"[USER STORE] Auth error: {e}")
        return None, None


def normalize_phone(phone_input):
    """Normalize phone input to +1XXXXXXXXXX format"""
    digits = re.sub(r'\D', '', phone_input)
    if len(digits) == 10:
        return f"+1{digits}"
    elif len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return None


def validate_phone(normalized):
    """Check if normalized phone is valid"""
    return normalized is not None and len(normalized) == 12 and normalized.startswith("+1")


# --- Customer CRUD ---

def get_customer(phone):
    """Look up customer by phone. Returns dict or None."""
    _, spreadsheet = _get_sheets_client()
    if not spreadsheet:
        return None

    try:
        ws = spreadsheet.worksheet("customers")
        cell = ws.find(phone, in_column=1)
        if not cell:
            return None

        row = ws.row_values(cell.row)
        headers = ws.row_values(1)

        # Pad row to match headers length
        while len(row) < len(headers):
            row.append("")

        return {headers[i]: row[i] for i in range(len(headers))}
    except Exception as e:
        print(f"[USER STORE] Error getting customer: {e}")
        return None


def create_customer(phone):
    """Create new customer row. Returns the customer dict."""
    _, spreadsheet = _get_sheets_client()
    if not spreadsheet:
        return {"phone": phone, "total_visits": "1", "opted_out": "FALSE"}

    try:
        ws = spreadsheet.worksheet("customers")
        now = datetime.now().isoformat()
        row = [phone, "", now, now, "1", "", "", "", "", "FALSE"]
        ws.append_row(row, value_input_option="RAW")

        headers = ws.row_values(1)
        return {headers[i]: row[i] for i in range(len(headers))}
    except Exception as e:
        print(f"[USER STORE] Error creating customer: {e}")
        return {"phone": phone, "total_visits": "1", "opted_out": "FALSE"}


def update_customer(phone, updates):
    """Update specific fields for a customer."""
    _, spreadsheet = _get_sheets_client()
    if not spreadsheet:
        return

    try:
        ws = spreadsheet.worksheet("customers")
        cell = ws.find(phone, in_column=1)
        if not cell:
            return

        headers = ws.row_values(1)
        for key, value in updates.items():
            if key in headers:
                col_idx = headers.index(key) + 1
                ws.update_cell(cell.row, col_idx, value)
    except Exception as e:
        print(f"[USER STORE] Error updating customer: {e}")


def increment_visit(phone):
    """Bump total_visits and update last_seen."""
    _, spreadsheet = _get_sheets_client()
    if not spreadsheet:
        return

    try:
        ws = spreadsheet.worksheet("customers")
        cell = ws.find(phone, in_column=1)
        if not cell:
            return

        headers = ws.row_values(1)
        row = ws.row_values(cell.row)
        while len(row) < len(headers):
            row.append("")

        # Update visit count
        visits_col = headers.index("total_visits") + 1
        current_visits = int(row[headers.index("total_visits")] or "0")
        ws.update_cell(cell.row, visits_col, str(current_visits + 1))

        # Update last_seen
        last_seen_col = headers.index("last_seen") + 1
        ws.update_cell(cell.row, last_seen_col, datetime.now().isoformat())
    except Exception as e:
        print(f"[USER STORE] Error incrementing visit: {e}")


# --- Visit tracking ---

def log_visit(phone, visit_id, table_id="Unknown"):
    """Append a new visit row."""
    _, spreadsheet = _get_sheets_client()
    if not spreadsheet:
        return

    try:
        ws = spreadsheet.worksheet("visits")
        row = [visit_id, phone, datetime.now().isoformat(), "", table_id]
        ws.append_row(row, value_input_option="RAW")
    except Exception as e:
        print(f"[USER STORE] Error logging visit: {e}")


def add_game_to_visit(visit_id, game_title):
    """Append game title to games_played for this visit."""
    _, spreadsheet = _get_sheets_client()
    if not spreadsheet:
        return

    try:
        ws = spreadsheet.worksheet("visits")
        cell = ws.find(visit_id, in_column=1)
        if not cell:
            return

        headers = ws.row_values(1)
        row = ws.row_values(cell.row)
        while len(row) < len(headers):
            row.append("")

        games_col = headers.index("games_played") + 1
        current_games = row[headers.index("games_played")]
        if current_games:
            if game_title not in current_games.split(", "):
                ws.update_cell(cell.row, games_col, f"{current_games}, {game_title}")
        else:
            ws.update_cell(cell.row, games_col, game_title)
    except Exception as e:
        print(f"[USER STORE] Error adding game to visit: {e}")


def get_visit_history(phone, limit=10):
    """Get recent visits for a customer. Most recent first."""
    _, spreadsheet = _get_sheets_client()
    if not spreadsheet:
        return []

    try:
        ws = spreadsheet.worksheet("visits")
        all_records = ws.get_all_records()

        visits = [r for r in all_records if r.get("phone") == phone]
        visits.sort(key=lambda v: v.get("started_at", ""), reverse=True)
        return visits[:limit]
    except Exception as e:
        print(f"[USER STORE] Error getting visit history: {e}")
        return []


# --- Preferences ---

def update_preferences(phone, dietary=None, game_prefs=None,
                       experience=None, notable_info=None):
    """Update preference fields. Merges with existing values."""
    _, spreadsheet = _get_sheets_client()
    if not spreadsheet:
        return

    try:
        ws = spreadsheet.worksheet("customers")
        cell = ws.find(phone, in_column=1)
        if not cell:
            return

        headers = ws.row_values(1)
        row = ws.row_values(cell.row)
        while len(row) < len(headers):
            row.append("")

        def merge_csv(existing, new_value):
            """Merge comma-separated values without duplicates"""
            existing_set = set(v.strip() for v in existing.split(",") if v.strip())
            new_set = set(v.strip() for v in new_value.split(",") if v.strip())
            return ", ".join(sorted(existing_set | new_set))

        if dietary:
            col = headers.index("dietary_preferences") + 1
            current = row[headers.index("dietary_preferences")]
            ws.update_cell(cell.row, col, merge_csv(current, dietary))

        if game_prefs:
            col = headers.index("game_preferences") + 1
            current = row[headers.index("game_preferences")]
            ws.update_cell(cell.row, col, merge_csv(current, game_prefs))

        if experience:
            col = headers.index("experience_level") + 1
            ws.update_cell(cell.row, col, experience)

        if notable_info:
            col = headers.index("notable_info") + 1
            current = row[headers.index("notable_info")]
            if current:
                ws.update_cell(cell.row, col, f"{current}; {notable_info}")
            else:
                ws.update_cell(cell.row, col, notable_info)
    except Exception as e:
        print(f"[USER STORE] Error updating preferences: {e}")


# --- Ratings ---

def log_rating(phone, game_title, rating):
    """Append a rating row."""
    _, spreadsheet = _get_sheets_client()
    if not spreadsheet:
        return

    try:
        ws = spreadsheet.worksheet("ratings")
        row = [str(uuid.uuid4()), phone, game_title, str(rating), datetime.now().isoformat()]
        ws.append_row(row, value_input_option="RAW")
    except Exception as e:
        print(f"[USER STORE] Error logging rating: {e}")


# --- History context for Claude ---

def build_history_context(phone):
    """Build a structured text block for injection into Claude's prompt."""
    if not phone or phone == "ANON":
        return ""

    customer = get_customer(phone)
    if not customer:
        return ""

    visits = get_visit_history(phone, limit=5)

    lines = ["CUSTOMER CONTEXT (use this to personalize, but don't recite it back):"]

    total_visits = customer.get("total_visits", "1")
    if int(total_visits) > 1:
        lines.append(f"- Returning customer, visit #{total_visits}")
    else:
        lines.append("- First-time customer")

    if customer.get("dietary_preferences"):
        lines.append(f"- Dietary: {customer['dietary_preferences']}")

    if customer.get("experience_level"):
        lines.append(f"- Experience level: {customer['experience_level']}")

    # Aggregate games from visit history
    game_counts = {}
    for visit in visits:
        games = visit.get("games_played", "")
        if games:
            for game in games.split(", "):
                game = game.strip()
                if game:
                    game_counts[game] = game_counts.get(game, 0) + 1

    if game_counts:
        game_str = ", ".join(f"{g} ({c}x)" if c > 1 else g for g, c in
                            sorted(game_counts.items(), key=lambda x: -x[1]))
        lines.append(f"- Previously played: {game_str}")

    if customer.get("game_preferences"):
        lines.append(f"- Preferred game types: {customer['game_preferences']}")

    if customer.get("notable_info"):
        lines.append(f"- Notable: {customer['notable_info']}")

    return "\n".join(lines)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    print("Testing user store...")

    test_phone = normalize_phone("7185551234")
    print(f"Normalized: {test_phone}")
    print(f"Valid: {validate_phone(test_phone)}")

    customer = get_customer(test_phone)
    if customer:
        print(f"Found customer: {customer}")
    else:
        print("Customer not found, creating...")
        customer = create_customer(test_phone)
        print(f"Created: {customer}")

    print("\nHistory context:")
    print(build_history_context(test_phone))
