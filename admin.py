"""
Merry Meeple Staff Admin Dashboard
Manage tables, monitor active sessions, track orders, and control customer sessions.
Access via: streamlit run admin.py
Protected by admin password.
"""

import streamlit as st
import os
import json
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv
from database import init_database, DB_PATH
from user_store import get_customer

load_dotenv()

# --- Config ---
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "merrymeeple2026")
TOTAL_TABLES = int(os.environ.get("TOTAL_TABLES", "12"))

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
    """Link a session to a table."""
    conn = get_db()
    conn.execute("""
        UPDATE active_sessions SET table_number = ? WHERE visit_id = ?
    """, (table_num, visit_id))
    conn.execute("""
        UPDATE tables SET visit_id = ? WHERE table_number = ?
    """, (visit_id, table_num))
    conn.commit()
    conn.close()


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
        SELECT COUNT(*) as cnt FROM active_sessions
        WHERE status = 'active'
    """).fetchone()
    stats["active_sessions"] = row["cnt"]

    row = conn.execute("""
        SELECT COUNT(*) as cnt FROM tables WHERE status = 'occupied'
    """).fetchone()
    stats["occupied_tables"] = row["cnt"]
    stats["total_tables"] = TOTAL_TABLES

    conn.close()
    return stats


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
                phone = table.get("phone", "Unknown")
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

                st.markdown(
                    f"""<div style="background:#2d5016; border-radius:10px; padding:12px;
                    margin-bottom:8px; border: 2px solid #4a8c1c;">
                    <b>Table {tnum}</b> 🟢<br>
                    📱 {phone[-4:] if phone and len(phone) >= 4 else '?'}<br>
                    👥 {party} · ⏱️ {time_str}
                    </div>""",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"""<div style="background:#1a1a2e; border-radius:10px; padding:12px;
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
    """Active sessions monitor."""
    st.subheader("📡 Active Sessions")

    sessions = get_active_sessions()
    if not sessions:
        st.info("No active sessions right now.")
        return

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

        col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 2, 1])
        with col1:
            display_phone = f"...{phone[-4:]}" if phone and len(phone) >= 4 else phone
            st.markdown(f"📱 **{display_phone}**")
        with col2:
            st.markdown(f"🪑 Table {table or '?'}")
        with col3:
            st.markdown(f"⏱️ {time_str}")
        with col4:
            st.markdown(f"🎮 {game or 'No game'} · Last: {idle_str}")
        with col5:
            if st.button("End Session", key=f"kill_{visit_id}", type="primary"):
                kill_session(visit_id)
                st.success(f"Session ended for {display_phone}")
                st.rerun()

        st.divider()


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

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Active Sessions", stats["active_sessions"])
    with col2:
        st.metric("Tables Occupied",
                   f"{stats['occupied_tables']}/{stats['total_tables']}")
    with col3:
        st.metric("Total Orders", stats["total_orders"])
    with col4:
        st.metric("Pending Orders", stats["pending_orders"])
    with col5:
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


# --- Main ---

def main():
    init_database()
    init_admin_tables()

    if not check_admin_auth():
        return

    st.title("🎲 Merry Meeple — Staff Dashboard")

    # Top-level stats
    render_stats_view()
    st.divider()

    # Main tabs
    tab_floor, tab_orders, tab_sessions = st.tabs(
        ["🪑 Floor", "🍽️ Orders", "📡 Sessions"]
    )

    with tab_floor:
        render_floor_view()

    with tab_orders:
        render_orders_view()

    with tab_sessions:
        render_sessions_view()

    # Auto-refresh every 30 seconds
    st.markdown(
        """<meta http-equiv="refresh" content="30">""",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
