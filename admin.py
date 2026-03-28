"""
Merry Meeple Staff Admin Dashboard
Manage tables, monitor active sessions, track orders, and control customer sessions.
Access via: streamlit run admin.py
Protected by admin password.
"""

import streamlit as st
from streamlit_autorefresh import st_autorefresh
import os
import json
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv
from database import init_database, DB_PATH, get_all_games
from user_store import get_customer

load_dotenv()

# --- Config ---
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "merrymeeple2026")
TOTAL_TABLES = int(os.environ.get("TOTAL_TABLES", "12"))

# Only set page config when running standalone (not embedded in app.py)
if __name__ == "__main__":
    st.set_page_config(
        page_title="Merry Meeple — Staff Dashboard",
        page_icon="🎲",
        layout="wide",
    )


# --- DB helpers ---

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_admin_tables():
    """Create admin-specific tables if they don't exist."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tables (
            table_number INTEGER PRIMARY KEY,
            status TEXT DEFAULT 'available',
            phone TEXT,
            visit_id TEXT,
            party_size INTEGER DEFAULT 1,
            seated_at TIMESTAMP,
            notes TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS active_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visit_id TEXT UNIQUE NOT NULL,
            phone TEXT,
            table_number INTEGER,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            current_game TEXT,
            status TEXT DEFAULT 'active',
            killed_by TEXT,
            killed_at TIMESTAMP
        )
    """)

    # Make sure orders table has table_number and status tracking
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS order_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT UNIQUE NOT NULL,
            phone TEXT,
            visit_id TEXT,
            table_number INTEGER,
            items TEXT NOT NULL,
            subtotal REAL,
            discount REAL DEFAULT 0,
            tax REAL DEFAULT 0,
            total REAL NOT NULL,
            status TEXT DEFAULT 'pending',
            staff_notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            accepted_at TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)

    # Seed table rows if empty
    cursor.execute("SELECT COUNT(*) FROM tables")
    if cursor.fetchone()[0] == 0:
        for i in range(1, TOTAL_TABLES + 1):
            cursor.execute(
                "INSERT OR IGNORE INTO tables (table_number) VALUES (?)", (i,)
            )

    conn.commit()
    conn.close()


def get_tables():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM tables ORDER BY table_number"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def seat_table(table_num, phone, party_size, notes=""):
    conn = get_db()
    conn.execute("""
        UPDATE tables SET status='occupied', phone=?, party_size=?,
            seated_at=CURRENT_TIMESTAMP, notes=?
        WHERE table_number=?
    """, (phone, party_size, notes, table_num))
    conn.commit()
    conn.close()


def clear_table(table_num):
    conn = get_db()
    conn.execute("""
        UPDATE tables SET status='available', phone=NULL, visit_id=NULL,
            party_size=1, seated_at=NULL, notes=NULL
        WHERE table_number=?
    """, (table_num,))
    # End all active sessions at this table
    conn.execute("""
        UPDATE active_sessions SET status='ended', killed_at=CURRENT_TIMESTAMP
        WHERE table_number=? AND status='active'
    """, (table_num,))
    conn.commit()
    conn.close()


def get_active_sessions():
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM active_sessions
        WHERE status = 'active'
        ORDER BY started_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def kill_session(visit_id, staff_name="staff"):
    conn = get_db()
    conn.execute("""
        UPDATE active_sessions SET status='killed', killed_by=?,
            killed_at=CURRENT_TIMESTAMP
        WHERE visit_id=?
    """, (staff_name, visit_id))
    conn.commit()
    conn.close()


def get_orders(status_filter=None, limit=50):
    conn = get_db()
    if status_filter:
        rows = conn.execute("""
            SELECT * FROM order_queue
            WHERE status = ?
            ORDER BY created_at DESC LIMIT ?
        """, (status_filter, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM order_queue
            ORDER BY created_at DESC LIMIT ?
        """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_order_status(order_id, new_status, staff_notes=None):
    conn = get_db()
    if new_status == "accepted":
        conn.execute("""
            UPDATE order_queue SET status=?, staff_notes=?,
                accepted_at=CURRENT_TIMESTAMP
            WHERE order_id=?
        """, (new_status, staff_notes, order_id))
    elif new_status == "completed":
        conn.execute("""
            UPDATE order_queue SET status=?, staff_notes=?,
                completed_at=CURRENT_TIMESTAMP
            WHERE order_id=?
        """, (new_status, staff_notes, order_id))
    else:
        conn.execute("""
            UPDATE order_queue SET status=?, staff_notes=?
            WHERE order_id=?
        """, (new_status, staff_notes, order_id))
    conn.commit()
    conn.close()


def get_session_for_phone(phone):
    """Find active session for a phone number."""
    conn = get_db()
    row = conn.execute("""
        SELECT * FROM active_sessions
        WHERE phone = ? AND status = 'active'
        ORDER BY started_at DESC LIMIT 1
    """, (phone,)).fetchone()
    conn.close()
    return dict(row) if row else None


def link_session_to_table(visit_id, table_num):
    """Link a session to a table. Multiple sessions can share a table."""
    conn = get_db()
    conn.execute("""
        UPDATE active_sessions SET table_number = ? WHERE visit_id = ?
    """, (table_num, visit_id))
    # Update party size to reflect all active sessions at this table
    count = conn.execute("""
        SELECT COUNT(*) as cnt FROM active_sessions
        WHERE table_number = ? AND status = 'active'
    """, (table_num,)).fetchone()["cnt"]
    conn.execute("""
        UPDATE tables SET party_size = ? WHERE table_number = ?
    """, (max(count, 1), table_num))
    conn.commit()
    conn.close()


def get_sessions_at_table(table_num):
    """Get all active sessions at a given table."""
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM active_sessions
        WHERE table_number = ? AND status = 'active'
        ORDER BY started_at ASC
    """, (table_num,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_table_orders(table_num):
    """Get all orders for a specific table in current session."""
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM order_queue
        WHERE table_number = ?
          AND date(created_at) = date('now')
        ORDER BY created_at ASC
    """, (table_num,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_daily_stats():
    """Get summary stats for today."""
    conn = get_db()
    stats = {}

    row = conn.execute("""
        SELECT COUNT(*) as cnt, COALESCE(SUM(total), 0) as rev
        FROM order_queue WHERE date(created_at) = date('now')
    """).fetchone()
    stats["total_orders"] = row["cnt"]
    stats["total_revenue"] = row["rev"]

    row = conn.execute("""
        SELECT COUNT(*) as cnt FROM order_queue
        WHERE date(created_at) = date('now') AND status = 'pending'
    """).fetchone()
    stats["pending_orders"] = row["cnt"]

    row = conn.execute("""
        SELECT COUNT(*) as cnt FROM order_queue
        WHERE date(created_at) = date('now') AND status IN ('pending', 'accepted')
    """).fetchone()
    stats["live_orders"] = row["cnt"]

    row = conn.execute("""
        SELECT COUNT(*) as cnt FROM active_sessions
        WHERE status = 'active'
    """).fetchone()
    stats["active_sessions"] = row["cnt"]

    row = conn.execute("""
        SELECT COUNT(*) as cnt FROM tables WHERE status = 'occupied'
    """).fetchone()
    stats["occupied_tables"] = row["cnt"]
    stats["total_tables"] = TOTAL_TABLES

    row = conn.execute("""
        SELECT COUNT(*) as cnt FROM staff_requests
        WHERE status = 'pending'
    """).fetchone()
    stats["pending_pings"] = row["cnt"]

    conn.close()
    return stats


def get_pending_staff_requests():
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM staff_requests
        WHERE status = 'pending'
        ORDER BY created_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def acknowledge_staff_request(request_id):
    conn = get_db()
    conn.execute("""
        UPDATE staff_requests SET status='acknowledged',
            acknowledged_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (request_id,))
    conn.commit()
    conn.close()


def assign_game_to_session(visit_id, game_title):
    conn = get_db()
    conn.execute("""
        UPDATE active_sessions SET current_game=? WHERE visit_id=?
    """, (game_title, visit_id))
    conn.commit()
    conn.close()


# --- Auth ---

def check_admin_auth():
    if "admin_authenticated" not in st.session_state:
        st.session_state.admin_authenticated = False

    if not st.session_state.admin_authenticated:
        st.title("🎲 Merry Meeple — Staff Login")
        with st.form("admin_login_form"):
            password = st.text_input("Staff password", type="password")
            submitted = st.form_submit_button("Log in", use_container_width=True)
        if submitted:
            if password == ADMIN_PASSWORD:
                st.session_state.admin_authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password.")
        return False
    return True


# --- Main UI ---

REASON_ICONS = {
    "rules_question": "📖",
    "food_order": "🍽️",
    "new_game": "🎮",
    "general_help": "🙋",
}


def render_staff_pings():
    """Show pending staff requests as prominent alert banners."""
    requests = get_pending_staff_requests()
    if not requests:
        return

    st.markdown(
        f"""<div style="background:#b91c1c; color:#fff; padding:10px 16px;
        border-radius:8px; margin-bottom:12px; font-weight:bold; font-size:1.1em;">
        🔔 {len(requests)} pending staff request{'s' if len(requests) != 1 else ''}
        </div>""",
        unsafe_allow_html=True,
    )

    for req in requests:
        rid = req["id"]
        reason = req.get("reason", "general_help")
        icon = REASON_ICONS.get(reason, "🔔")
        phone = req.get("phone", "")
        display_phone = f"...{phone[-4:]}" if phone and len(phone) >= 4 else phone or "?"
        table = req.get("table_number")
        game = req.get("game_title") or ""
        question = req.get("question", "")[:80]
        created = req.get("created_at", "")

        try:
            created_dt = datetime.fromisoformat(created)
            elapsed = datetime.now() - created_dt
            mins = int(elapsed.total_seconds()) // 60
            if mins < 1:
                time_str = "just now"
            elif mins < 60:
                time_str = f"{mins}m ago"
            else:
                time_str = f"{mins // 60}h {mins % 60}m ago"
        except (ValueError, TypeError):
            time_str = "?"

        table_display = f"TABLE {table}" if table else "TABLE ?"
        col1, col2 = st.columns([4, 1])
        with col1:
            st.markdown(
                f"""<div style="background:#7f1d1d; color:#fff; padding:12px 14px;
                border-radius:8px; margin-bottom:6px;">
                <span style="font-size:1.4em; font-weight:bold;">🪑 {table_display}</span>
                &nbsp;&nbsp;{icon} {reason.replace('_', ' ').title()} ·
                📱 {display_phone} ·
                🎮 {game or 'N/A'} ·
                ⏱️ {time_str}<br>
                <span style="color:#fca5a5; font-size:1.1em; font-weight:bold;">{question}</span>
                </div>""",
                unsafe_allow_html=True,
            )
        with col2:
            st.markdown("")  # spacer for vertical alignment
            if st.button("✅ Acknowledge", key=f"ack_{rid}", use_container_width=True):
                acknowledge_staff_request(rid)
                st.rerun()

    st.divider()


def render_floor_view():
    """Table map and management."""
    st.subheader("🪑 Floor View")

    tables = get_tables()

    # Table grid (4 columns)
    cols = st.columns(4)
    for i, table in enumerate(tables):
        with cols[i % 4]:
            tnum = table["table_number"]
            status = table["status"]

            if status == "occupied":
                party = table.get("party_size", 1)
                seated = table.get("seated_at", "")
                if seated:
                    try:
                        seated_dt = datetime.fromisoformat(seated)
                        elapsed = datetime.now() - seated_dt
                        hours, remainder = divmod(int(elapsed.total_seconds()), 3600)
                        minutes = remainder // 60
                        time_str = f"{hours}h {minutes}m" if hours else f"{minutes}m"
                    except (ValueError, TypeError):
                        time_str = "?"
                else:
                    time_str = "?"

                # Show active sessions (devices) at this table
                sessions_here = get_sessions_at_table(tnum)
                session_count = len(sessions_here)
                phones_display = ", ".join(
                    f"...{s['phone'][-4:]}" for s in sessions_here
                    if s.get("phone") and len(s["phone"]) >= 4
                ) or "?"

                st.markdown(
                    f"""<div style="background:#2d5016; color:#fff; border-radius:10px; padding:12px;
                    margin-bottom:8px; border: 2px solid #4a8c1c;">
                    <b>Table {tnum}</b> 🟢<br>
                    📱 {phones_display}<br>
                    👥 {party} · 📲 {session_count} · ⏱️ {time_str}
                    </div>""",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"""<div style="background:#1a1a2e; color:#ccc; border-radius:10px; padding:12px;
                    margin-bottom:8px; border: 2px solid #333;">
                    <b>Table {tnum}</b> ⚪<br>
                    <span style="color:#888;">Available</span><br>
                    &nbsp;
                    </div>""",
                    unsafe_allow_html=True,
                )

    # Table actions
    st.divider()
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Seat a Party**")
        avail_tables = [t["table_number"] for t in tables if t["status"] == "available"]
        if avail_tables:
            seat_table_num = st.selectbox("Table", avail_tables, key="seat_table")
            seat_phone = st.text_input("Phone (optional)", key="seat_phone")
            seat_party = st.number_input("Party size", min_value=1, max_value=20,
                                          value=2, key="seat_party")
            seat_notes = st.text_input("Notes", key="seat_notes")
            if st.button("Seat Party", use_container_width=True):
                seat_table(seat_table_num, seat_phone or "walk-in", seat_party, seat_notes)

                # Auto-link if there's an active session for this phone
                if seat_phone:
                    from user_store import normalize_phone
                    norm = normalize_phone(seat_phone)
                    if norm:
                        session = get_session_for_phone(norm)
                        if session:
                            link_session_to_table(session["visit_id"], seat_table_num)

                st.success(f"Table {seat_table_num} seated!")
                st.rerun()
        else:
            st.info("No tables available.")

    with col2:
        st.markdown("**Clear a Table**")
        occupied_tables = [t["table_number"] for t in tables if t["status"] == "occupied"]
        if occupied_tables:
            clear_table_num = st.selectbox("Table", occupied_tables, key="clear_table")
            if st.button("Clear Table", type="primary", use_container_width=True):
                clear_table(clear_table_num)
                st.success(f"Table {clear_table_num} cleared.")
                st.rerun()
        else:
            st.info("No tables to clear.")


def render_sessions_view():
    """Active sessions monitor with assign controls."""
    st.subheader("📡 Active Sessions")

    sessions = get_active_sessions()
    if not sessions:
        st.info("No active sessions right now.")
        return

    tables = get_tables()
    avail_tables = [t["table_number"] for t in tables if t["status"] == "available"]
    all_games = get_all_games()
    game_titles = ["(none)"] + [g["title"] for g in all_games]

    for session in sessions:
        visit_id = session["visit_id"]
        phone = session.get("phone", "Unknown")
        table = session.get("table_number")
        started = session.get("started_at", "")
        game = session.get("current_game", "")
        last_activity = session.get("last_activity", "")

        # Calculate time
        try:
            started_dt = datetime.fromisoformat(started)
            elapsed = datetime.now() - started_dt
            hours, remainder = divmod(int(elapsed.total_seconds()), 3600)
            minutes = remainder // 60
            time_str = f"{hours}h {minutes}m" if hours else f"{minutes}m"
        except (ValueError, TypeError):
            time_str = "?"

        # Calculate idle time
        try:
            last_dt = datetime.fromisoformat(last_activity)
            idle = datetime.now() - last_dt
            idle_min = int(idle.total_seconds()) // 60
            idle_str = f"{idle_min}m ago" if idle_min < 60 else f"{idle_min // 60}h ago"
        except (ValueError, TypeError):
            idle_str = "?"

        display_phone = f"...{phone[-4:]}" if phone and len(phone) >= 4 else phone
        header = f"📱 {display_phone} · 🪑 Table {table or '?'} · ⏱️ {time_str} · 🎮 {game or 'No game'} · Last: {idle_str}"

        with st.expander(header, expanded=False):
            # Assign table (can assign to available or occupied tables)
            st.markdown("**Assign Table**")
            if table:
                st.caption(f"Currently at Table {table}")
            all_table_nums = [t["table_number"] for t in tables]
            if all_table_nums:
                # Show all tables, label occupied ones
                table_labels = {}
                for t in tables:
                    tn = t["table_number"]
                    if t["status"] == "occupied":
                        table_labels[tn] = f"Table {tn} (occupied, {t.get('party_size', '?')} guests)"
                    else:
                        table_labels[tn] = f"Table {tn}"
                tcol1, tcol2, tcol3 = st.columns([1, 1, 1])
                with tcol1:
                    sel_table = st.selectbox(
                        "Table", all_table_nums,
                        format_func=lambda x: table_labels.get(x, f"Table {x}"),
                        key=f"sess_tbl_{visit_id}")
                with tcol2:
                    party_size = st.number_input("Party size", min_value=1,
                                                  max_value=20, value=2,
                                                  key=f"sess_party_{visit_id}")
                with tcol3:
                    st.markdown("")  # spacer
                    if st.button("Assign Table", key=f"sess_assign_{visit_id}",
                                  use_container_width=True):
                        # Only seat if table isn't already occupied
                        tbl_info = next((t for t in tables if t["table_number"] == sel_table), None)
                        if not tbl_info or tbl_info["status"] != "occupied":
                            seat_table(sel_table, phone or "walk-in", party_size)
                        link_session_to_table(visit_id, sel_table)
                        st.success(f"Assigned to Table {sel_table}")

            st.markdown("**Assign Game**")
            gcol1, gcol2 = st.columns([2, 1])
            with gcol1:
                current_idx = game_titles.index(game) if game in game_titles else 0
                sel_game = st.selectbox("Game", game_titles, index=current_idx,
                                         key=f"sess_game_{visit_id}")
            with gcol2:
                st.markdown("")  # spacer
                if st.button("Set Game", key=f"sess_setgame_{visit_id}",
                              use_container_width=True):
                    new_game = sel_game if sel_game != "(none)" else None
                    assign_game_to_session(visit_id, new_game)
                    st.success(f"Game set to {new_game or 'none'}")

            st.divider()
            if st.button("End Session", key=f"kill_{visit_id}", type="primary",
                          use_container_width=True):
                kill_session(visit_id)
                st.success(f"Session ended for {display_phone}")
                st.rerun()


def render_orders_view():
    """Order queue for staff."""
    st.subheader("🍽️ Order Queue")

    # Status filter tabs
    tab_pending, tab_accepted, tab_completed, tab_all = st.tabs(
        ["⏳ Pending", "🔥 In Progress", "✅ Completed", "📋 All"]
    )

    with tab_pending:
        orders = get_orders(status_filter="pending")
        if not orders:
            st.info("No pending orders. 🎉")
        for order in orders:
            render_order_card(order, show_actions=True)

    with tab_accepted:
        orders = get_orders(status_filter="accepted")
        if not orders:
            st.info("No orders in progress.")
        for order in orders:
            render_order_card(order, show_complete=True)

    with tab_completed:
        orders = get_orders(status_filter="completed", limit=20)
        if not orders:
            st.info("No completed orders today.")
        for order in orders:
            render_order_card(order)

    with tab_all:
        orders = get_orders(limit=50)
        for order in orders:
            render_order_card(order)


def render_order_card(order, show_actions=False, show_complete=False):
    """Render a single order card."""
    order_id = order["order_id"]
    table = order.get("table_number", "?")
    phone = order.get("phone", "")
    total = order.get("total", 0)
    status = order.get("status", "pending")
    created = order.get("created_at", "")

    # Parse items
    try:
        items = json.loads(order.get("items", "[]"))
    except (json.JSONDecodeError, TypeError):
        items = []

    # Time since order
    try:
        created_dt = datetime.fromisoformat(created)
        elapsed = datetime.now() - created_dt
        minutes = int(elapsed.total_seconds()) // 60
        if minutes < 1:
            time_str = "just now"
        elif minutes < 60:
            time_str = f"{minutes}m ago"
        else:
            time_str = f"{minutes // 60}h {minutes % 60}m ago"
    except (ValueError, TypeError):
        time_str = "?"

    # Status colors
    status_colors = {
        "pending": "🟡",
        "accepted": "🔵",
        "completed": "🟢",
        "cancelled": "🔴",
    }
    status_icon = status_colors.get(status, "⚪")

    display_phone = f"...{phone[-4:]}" if phone and len(phone) >= 4 else phone or "?"

    st.markdown(f"""
    **{status_icon} Order #{order_id[:8]}** · Table {table} · {display_phone} · {time_str}
    """)

    # Item list
    for item in items:
        name = item.get("name", "?")
        qty = item.get("qty", 1)
        price = item.get("price", 0)
        notes = item.get("notes", "")
        options = item.get("options", "")
        line = f"  - {qty}x **{name}** — ${price:.2f}"
        if options:
            line += f" ({options})"
        if notes:
            line += f" *— {notes}*"
        st.markdown(line)

    st.markdown(f"  **Total: ${total:.2f}**")

    if show_actions:
        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ Accept", key=f"accept_{order_id}",
                          use_container_width=True):
                update_order_status(order_id, "accepted")
                st.rerun()
        with col2:
            if st.button("❌ Cancel", key=f"cancel_{order_id}",
                          use_container_width=True):
                update_order_status(order_id, "cancelled")
                st.rerun()

    if show_complete:
        if st.button("✅ Mark Complete", key=f"complete_{order_id}",
                      use_container_width=True):
            update_order_status(order_id, "completed")
            st.rerun()

    st.divider()


def render_stats_view():
    """Daily overview stats."""
    st.subheader("📊 Today's Overview")

    stats = get_daily_stats()

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    with col1:
        st.metric("Staff Pings", stats.get("pending_pings", 0))
    with col2:
        st.metric("Active Sessions", stats["active_sessions"])
    with col3:
        st.metric("Tables Occupied",
                   f"{stats['occupied_tables']}/{stats['total_tables']}")
    with col4:
        st.metric("Total Orders", stats["total_orders"])
    with col5:
        st.metric("Pending Orders", stats["pending_orders"])
    with col6:
        st.metric("Revenue Today", f"${stats['total_revenue']:.2f}")

    # Recent security events
    conn = get_db()
    events = conn.execute("""
        SELECT * FROM security_log
        WHERE date(created_at) = date('now')
        ORDER BY created_at DESC LIMIT 10
    """).fetchall()
    conn.close()

    if events:
        st.divider()
        st.markdown("**🔒 Security Events Today**")
        for event in events:
            event = dict(event)
            st.caption(
                f"{event.get('created_at', '?')} · {event.get('event_type', '?')} · "
                f"Phone: {event.get('phone', '?')} · {event.get('details', '')}"
            )

    return stats


# --- Main ---

def run_admin_dashboard():
    """Render the admin dashboard. Can be called from app.py or standalone.
    Assumes init_database() has already been called and page_config is set."""
    init_admin_tables()

    if not check_admin_auth():
        return

    st.title("🎲 Merry Meeple — Staff Dashboard")

    # Staff ping alerts (prominent, before everything else)
    render_staff_pings()

    # Top-level stats
    stats = render_stats_view()
    st.divider()

    # Main tabs (with live counts)
    tab_floor, tab_orders, tab_sessions = st.tabs([
        f"🪑 Floor ({stats['occupied_tables']})",
        f"🍽️ Orders ({stats['live_orders']})",
        f"📡 Sessions ({stats['active_sessions']})",
    ])

    with tab_floor:
        render_floor_view()

    with tab_orders:
        render_orders_view()

    with tab_sessions:
        render_sessions_view()

    # Auto-refresh every 30 seconds (uses Streamlit rerun, not full page reload)
    st_autorefresh(interval=10_000, key="admin_refresh")


def main():
    init_database()
    run_admin_dashboard()


if __name__ == "__main__":
    main()
