"""
The Merry Meeple — Rules Assistant.

QR-scoped rules Q&A. In production every game box has a QR that deep-links
into `/?g=<slug>`, dropping the customer straight into a chat that already
knows which game they're playing. Optional `?t=<n>` attaches the table
number so the "get staff help" button routes properly.

No `?g=` param → a testing game picker at the top of the page.

Above the chat there's a game-switcher dropdown for dev convenience; keep
it visible until we decide how to hide it in prod.
"""

import os
import re
import sqlite3
import time as _time

import numpy as np
import streamlit as st
import voyageai
from anthropic import Anthropic, APIStatusError
from dotenv import load_dotenv

from config import CLAUDE_MODEL, VOYAGE_MODEL
from database import (
    DB_PATH, get_all_games, get_chunks_including_parent, init_database,
)

load_dotenv(override=True)


TOP_K_RESULTS = 5
LANGUAGES = [
    ("🇺🇸", "English", "English"),
    ("🇪🇸", "Español", "Spanish"),
    ("🇨🇳", "中文", "Chinese"),
    ("🇷🇺", "Русский", "Russian"),
    ("🇭🇹", "Kreyòl Ayisyen", "Haitian Creole"),
    ("🇫🇷", "Français", "French"),
    ("🇮🇱", "עברית", "Hebrew"),
    ("🇸🇦", "العربية", "Arabic"),
    ("🇮🇹", "Italiano", "Italian"),
    ("🇵🇹", "Português", "Portuguese"),
]


# --------------------------------------------------------------------------
# Slug helpers — QR codes carry `?g=<slug>`; slug = lowercased, kebab-cased
# --------------------------------------------------------------------------

def title_to_slug(title):
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def slug_to_title(slug, all_titles):
    """Case- and punctuation-tolerant slug → title lookup."""
    if not slug:
        return None
    target = title_to_slug(slug)
    for title in all_titles:
        if title_to_slug(title) == target:
            return title
    return None


# --------------------------------------------------------------------------
# Anthropic retry wrapper — production Streamlit Cloud hit 529s often
# --------------------------------------------------------------------------

def anthropic_stream_with_retry(client, max_retries=3, **kwargs):
    for attempt in range(max_retries):
        try:
            with client.messages.stream(**kwargs) as stream:
                for text in stream.text_stream:
                    yield text
            return
        except APIStatusError as e:
            if e.status_code in (429, 529) and attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"[ANTHROPIC RETRY] {e.status_code}, waiting {wait}s")
                _time.sleep(wait)
            else:
                raise


# --------------------------------------------------------------------------
# RAG — semantic top-K cosine over pre-loaded chunks; per-game in-memory cache
# --------------------------------------------------------------------------
# Note: an earlier hybrid BM25 + semantic pass was tried and rolled back —
# it reduced deflects but at the cost of confidently-wrong regressions
# (fabricated quotes, mixed-rule confusion) that were worse for the customer
# than a safe deflection. retrieval.py stays in the repo for future
# experiments (e.g. hybrid at top-5, tighter anti-fabrication prompting).


def cosine_similarity(v1, v2):
    v1, v2 = np.array(v1), np.array(v2)
    return float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))


def search_chunks(query_embedding, chunks, top_k=TOP_K_RESULTS):
    scored = [(cosine_similarity(query_embedding, c["embedding"]), c) for c in chunks]
    scored.sort(reverse=True, key=lambda x: x[0])
    return [c for _, c in scored[:top_k]]


_chunk_cache = {}


def get_cached_chunks(game_title):
    if game_title not in _chunk_cache:
        _chunk_cache[game_title] = get_chunks_including_parent(game_title)
    return _chunk_cache[game_title]


def build_rules_prompt(question, game_title, top_chunks, language="English"):
    context_parts = []
    for c in top_chunks:
        source_label = {
            "rulebook": "Rulebook",
            "faq": "FAQ",
            "errata": "Errata",
            "supplement": "Supplement",
        }.get(c.get("source_type", "rulebook"), "Rulebook")
        # When we've merged in base-game chunks under an expansion, tag the
        # citation so the answer can say `Rulebook p. 5 (base Catan)` vs
        # `Rulebook p. 3 (Cities & Knights)`.
        origin = c.get("game_source")
        origin_tag = f" [{origin}]" if origin and origin != game_title else ""
        context_parts.append(
            f"[{source_label}{origin_tag} - Page {c['page']}]\n{c['text']}"
        )
    context = "\n\n---\n\n".join(context_parts)

    setup_kw = ["setup", "set up", "start", "beginning", "prepare",
                "how to play", "getting started"]
    is_setup = any(kw in question.lower() for kw in setup_kw)

    if is_setup:
        instruction = (
            "This is a SETUP question. Provide a complete, step-by-step walkthrough "
            "of the setup process. Use numbered steps, cover all components that "
            "need to be placed, mention player-specific setup, and cover any special "
            "setup rules for different player counts if mentioned."
        )
    else:
        instruction = "Provide a clear, direct answer to the specific question asked."

    lang_hint = (
        f"\nIMPORTANT: Respond entirely in {language}. Keep the game title in English."
        if language != "English" else ""
    )

    return f"""You are a helpful board game rules assistant at The Merry Meeple cafe. \
Answer the customer's question about **{game_title}** based ONLY on the source documents \
provided below.{lang_hint}

The sources may include:
- Rulebook (official game rules)
- FAQ (official frequently asked questions)
- Errata (official corrections/clarifications)
- Supplements (other official materials)

{instruction}

Rules for answering:
- Be friendly and conversational.
- When citing information, include BOTH the source type AND page number.
  Example: "According to the FAQ, nectar tokens can be spent as wild food (FAQ p. 2)"
  Example: "The rulebook states each player draws 5 cards (Rulebook p. 3)"
- Some sources may be tagged with a game name in brackets like "[Catan]" or "[Cities & Knights]" — this indicates whether the rule is from the base game or an expansion. If a rule comes from a base game (e.g., someone playing Catan: Cities & Knights asks about a rule that's in base Catan), say so explicitly: "This is a base Catan rule — the rulebook says... (Rulebook p. 5, base Catan)".
- If information comes from multiple sources, cite all of them.
- If the answer isn't in the provided sources, say: "I don't see that in the materials \
I have access to. Tap the '📞 Get staff help' button below if you'd like a staff \
member to come help you."
- If the question is unclear, ask ONE clarifying question.
- Never invent rules that aren't in the source documents.

SOURCE DOCUMENTS FOR {game_title.upper()}:
{context}

CUSTOMER QUESTION: {question}

YOUR ANSWER:"""


# --------------------------------------------------------------------------
# Staff ping — writes to staff_requests; admin dashboard polls that table
# --------------------------------------------------------------------------

def send_staff_ping(game_title, question, table_number=None, reason="rules_question"):
    """Write a pending staff request. Notification wiring is out of scope for now."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            """INSERT INTO staff_requests
               (visit_id, phone, table_number, game_title, question, reason)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (None, None, table_number, game_title, question, reason),
        )
        conn.commit()
        conn.close()
        print(f"[STAFF PING] table={table_number} game={game_title} reason={reason}")
        return True
    except Exception as e:
        print(f"[STAFF PING] DB error: {e}")
        return False


# --------------------------------------------------------------------------
# Streamlit UI
# --------------------------------------------------------------------------

st.set_page_config(
    page_title="The Merry Meeple — Rules Assistant",
    page_icon="🎲",
    layout="centered",
    initial_sidebar_state="collapsed",
)


@st.cache_resource
def init_clients():
    return (
        Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY")),
        voyageai.Client(api_key=os.environ.get("VOYAGE_API_KEY")),
    )


@st.cache_data
def load_game_library():
    init_database()
    return sorted(g["title"] for g in get_all_games())


anthropic_client, voyage_client = init_clients()
all_games = load_game_library()

if not all_games:
    st.error("📚 No games in the library yet. Run `process_rulebooks.py` to ingest PDFs.")
    st.stop()


# URL params
qp = st.query_params
g_param = qp.get("g")
t_param = qp.get("t")

try:
    table_number = int(t_param) if t_param else None
except (TypeError, ValueError):
    table_number = None

game_title = slug_to_title(g_param, all_games)


# --- No `?g=` — testing game picker ----------------------------------------

if not game_title:
    st.title("🎲 The Merry Meeple")
    st.markdown("### Pick a game to get started")
    st.caption(
        "In production, this page won't exist — customers scan a QR on the game "
        "box and land directly in that game's chat."
    )

    picked = st.selectbox(
        "Game",
        all_games,
        index=None,
        placeholder=f"Choose from {len(all_games)} games...",
        label_visibility="collapsed",
    )
    if picked:
        st.query_params["g"] = title_to_slug(picked)
        if table_number is not None:
            st.query_params["t"] = str(table_number)
        st.session_state.pop("messages", None)  # clear any stale chat
        st.rerun()
    st.stop()


# --- Chat surface ----------------------------------------------------------

# Reset chat when the game changes
if st.session_state.get("_active_game") != game_title:
    st.session_state._active_game = game_title
    st.session_state.messages = []
    st.session_state.language = "English"


# Header: game switcher (dev-visible) + language dropdown
c1, c2 = st.columns([3, 2])
with c1:
    switched = st.selectbox(
        "Currently helping with",
        all_games,
        index=all_games.index(game_title),
        key="game_switcher",
    )
    if switched != game_title:
        st.query_params["g"] = title_to_slug(switched)
        st.session_state.pop("messages", None)
        st.rerun()
with c2:
    lang_labels = [f"{flag}  {native}" for flag, native, _ in LANGUAGES]
    lang_names = [lang for _, _, lang in LANGUAGES]
    default_idx = lang_names.index(st.session_state.get("language", "English"))
    # Both selectboxes need visible labels so they align vertically on desktop.
    # With `collapsed`, the language box floats up above the game switcher.
    picked_label = st.selectbox("Language", lang_labels, index=default_idx)
    st.session_state.language = lang_names[lang_labels.index(picked_label)]


st.markdown(f"### {game_title}")


# Prime with an intro on first load
if not st.session_state.messages:
    st.session_state.messages.append({
        "role": "assistant",
        "content": (
            f"Hi! I can help with **{game_title}** rules — setup, edge cases, "
            f"clarifications, whatever's tripping you up. What would you like to know?"
        ),
    })


# Render history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        pages = msg.get("pages") or []
        if pages:
            st.caption(f"Sources: pages {', '.join(str(p) for p in pages)}")


# Input
user_input = st.chat_input(f"Ask anything about {game_title}...")

if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        with st.spinner("Consulting the rulebook..."):
            chunks = get_cached_chunks(game_title)
            q_emb = voyage_client.embed(
                texts=[user_input], model=VOYAGE_MODEL, input_type="query"
            ).embeddings[0]
            top = search_chunks(q_emb, chunks)
            prompt = build_rules_prompt(
                user_input, game_title, top,
                language=st.session_state.get("language", "English"),
            )
            source_pages = sorted({c["page"] for c in top})

        response_text = st.write_stream(
            anthropic_stream_with_retry(
                anthropic_client,
                model=CLAUDE_MODEL,
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
        )
        if source_pages:
            st.caption(f"Sources: pages {', '.join(str(p) for p in source_pages)}")

    st.session_state.messages.append({
        "role": "assistant",
        "content": response_text,
        "pages": source_pages,
    })


# --- Staff ping dialog + trigger button ------------------------------------

@st.dialog("Get staff help")
def staff_ping_dialog():
    """Confirm a staff ping and (if needed) collect the table number."""
    st.write(f"Ping staff to your table about **{game_title}**?")

    need_table = st.session_state.get("table_number") is None and table_number is None
    tbl_val = None
    if need_table:
        st.info("We need your table number first (check the sticker on your table).")
        tbl_val = st.text_input("Table number", placeholder="e.g. 5", key="_ping_tbl")

    left, right = st.columns(2)
    with left:
        if st.button("🚨 Yes, notify staff", use_container_width=True, key="_ping_yes"):
            tbl = table_number or st.session_state.get("table_number")
            if need_table:
                try:
                    n = int((tbl_val or "").strip())
                    if not (1 <= n <= 99):
                        st.error("Enter a number between 1 and 99.")
                        return
                    tbl = n
                    st.session_state.table_number = n
                except ValueError:
                    st.error("Please enter a valid table number.")
                    return

            last_user = next(
                (m["content"] for m in reversed(st.session_state.messages)
                 if m["role"] == "user"),
                "General help",
            )
            ok = send_staff_ping(game_title, last_user[:200], table_number=tbl)
            if ok:
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": "✅ Staff has been notified — someone will be with you shortly.",
                })
            st.session_state._show_ping = False
            st.rerun()
    with right:
        if st.button("Cancel", use_container_width=True, key="_ping_no"):
            st.session_state._show_ping = False
            st.rerun()


st.divider()
if st.button("📞 Get staff help", use_container_width=True):
    st.session_state._show_ping = True

if st.session_state.get("_show_ping"):
    staff_ping_dialog()
