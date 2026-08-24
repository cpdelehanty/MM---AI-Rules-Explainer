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


# Short affirmative/negative follow-ups that only make sense given prior context.
# If the current message is one of these (or otherwise very short), we
# concatenate it with the last real user turn for retrieval.
_FOLLOWUP_TOKENS = {
    "yes", "yeah", "yep", "yup", "sure", "correct", "right", "ok", "okay",
    "y", "no", "nope", "not really", "please", "yes please", "no thanks",
    "definitely", "of course",
}
_MAX_HISTORY_TURNS = 12  # cap the history we replay to Claude (keeps token cost sane)


def build_retrieval_query(user_input, prior_messages):
    """
    If the user's current input is a bare affirmative ("yes") or otherwise
    very short, embedding it directly retrieves noise. Augment with the
    most recent user turn so "yes" to "are you asking about setup?" still
    pulls setup chunks.
    """
    normalized = user_input.strip().lower().rstrip(".,!?")
    is_short = len(user_input.strip()) < 40
    is_followup = normalized in _FOLLOWUP_TOKENS
    if not (is_short or is_followup):
        return user_input
    # Walk back for the most recent user message that wasn't this one.
    for m in reversed(prior_messages):
        if m.get("role") == "user":
            return f"{m['content']} — follow-up: {user_input}"
    return user_input


def build_message_history(session_messages, current_prompt):
    """
    Assemble the messages array we send to Claude. All prior turns as they
    were rendered, then the current turn replaced with the RAG-wrapped
    prompt (so the answer sees the context + instructions freshly).

    `session_messages` includes the just-appended current user turn — we
    drop that copy and append the wrapped version instead.
    """
    prior = session_messages[:-1]
    # Trim to the most recent N turns
    if len(prior) > _MAX_HISTORY_TURNS:
        prior = prior[-_MAX_HISTORY_TURNS:]
    api_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in prior
        if m.get("role") in ("user", "assistant")
    ]
    api_messages.append({"role": "user", "content": current_prompt})
    return api_messages


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

    # Language directive: put it BOTH at the top (so it colors the whole
    # prompt) AND right before the answer (so it survives even when the
    # conversation history we send is entirely in English — Claude tends to
    # follow the language of the surrounding turns unless told otherwise).
    lang_hint_top = (
        f"\nIMPORTANT: Respond entirely in {language}. Keep the game title in English."
        if language != "English" else ""
    )
    lang_hint_bottom = (
        f"\n\nREMINDER: Regardless of what language earlier messages in this "
        f"conversation used, write YOUR ANSWER entirely in {language}. Do not "
        f"reply in English. Keep game titles, source labels (Rulebook/FAQ/Errata), "
        f"and page numbers as-is."
        if language != "English" else ""
    )

    return f"""You are a helpful board game rules assistant at The Merry Meeple cafe. \
Answer the customer's question about **{game_title}** based ONLY on the source documents \
provided below.{lang_hint_top}

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

CUSTOMER QUESTION: {question}{lang_hint_bottom}

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

BRAND_DIR = "assets/brand"

st.set_page_config(
    page_title="The Merry Meeple — Rules Assistant",
    page_icon=f"{BRAND_DIR}/favicon-32.png",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# --- Brand styling ---------------------------------------------------------
# Load Montserrat (per the brand guidelines — closest free match to Grift,
# the paid brand typeface). Override Streamlit's default red accents with
# the brand green (#2b4a3f). Amber (#c9922a) reserved for callouts.
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700&display=swap');

html, body, .stApp, .stApp *, .stMarkdown, .stMarkdown *,
button, input, textarea, select, [class*="st"] {
    font-family: 'Montserrat', -apple-system, BlinkMacSystemFont,
                 'Segoe UI', sans-serif !important;
}

/* Primary CTA + form submit -> brand green */
[data-testid="stBaseButton-primary"],
[data-testid="stFormSubmitButton"] > button {
    background-color: #2b4a3f !important;
    color: #FFFFFF !important;
    border-color: #2b4a3f !important;
}
[data-testid="stBaseButton-primary"]:hover,
[data-testid="stFormSubmitButton"] > button:hover {
    background-color: #1e3529 !important;
    border-color: #1e3529 !important;
}

/* Secondary buttons — subtle brand outline instead of Streamlit red */
[data-testid="stBaseButton-secondary"] {
    border-color: #2b4a3f !important;
    color: #2b4a3f !important;
}
[data-testid="stBaseButton-secondary"]:hover {
    background-color: #ede0c4 !important;   /* parchment on hover */
    color: #2b4a3f !important;
    border-color: #2b4a3f !important;
}

/* Chat input focus ring -> green */
[data-testid="stChatInput"] textarea:focus {
    border-color: #2b4a3f !important;
    box-shadow: 0 0 0 1px #2b4a3f !important;
}

/* Links */
a { color: #2b4a3f !important; }
a:hover { color: #c9922a !important; }

/* Headings — brand green, tighter tracking with Montserrat */
.stApp h1, .stApp h2, .stApp h3, .stApp h4,
.stMarkdown h1, .stMarkdown h2, .stMarkdown h3, .stMarkdown h4 {
    color: #2b4a3f !important;
    letter-spacing: -0.01em !important;
    font-weight: 700 !important;
}

/* Center the header logo we render below */
.mm-brand-header {
    display: flex;
    justify-content: center;
    padding: 0.25rem 0 0.75rem;
}
.mm-brand-header img {
    max-width: 320px;
    width: 100%;
    height: auto;
}
</style>
""", unsafe_allow_html=True)


def render_brand_header(width_px=280):
    """Centered primary logo. Used at the top of both the picker and chat views."""
    import base64
    with open(f"{BRAND_DIR}/primary-light.svg", "rb") as f:
        svg_b64 = base64.b64encode(f.read()).decode("ascii")
    st.markdown(
        f'<div class="mm-brand-header">'
        f'<img src="data:image/svg+xml;base64,{svg_b64}" '
        f'alt="The Merry Meeple" style="max-width:{width_px}px;">'
        f'</div>',
        unsafe_allow_html=True,
    )


# ------------------------------------------------------------------
# Mobile UX polish — the customer surface is 100% phone-first (QR
# from the game box). Streamlit's defaults have three papercuts on
# iOS Safari that we address here:
#
#   1. Tapping a form field triggers zoom that never restores.
#      Font-size 16px on all inputs is the standard suppression;
#      belt-and-suspenders: also disable programmatic zoom.
#   2. New messages don't scroll into view — user has to scroll
#      after every question. Auto-scroll on rerender fixes it.
#   3. When the mobile keyboard opens, Streamlit's `stBottom`
#      container leaves 56px of dead space below the input for the
#      iOS home indicator, which reads as awkward blank space.
#      Collapse that using env(safe-area-inset-bottom) so it only
#      matters when the keyboard is closed.
# ------------------------------------------------------------------
st.markdown("""
<style>
    /* iOS zoom prevention — belt-and-suspenders on top of font-size 16px */
    input[type="text"],
    input[role="combobox"],
    textarea,
    [data-testid="stChatInputTextArea"] {
        font-size: 16px !important;
        -webkit-text-size-adjust: 100%;
    }

    /* Use dynamic viewport height so the layout shrinks when the mobile
       keyboard opens. Streamlit defaults to vh which iOS doesn't shrink. */
    html, body, .stApp {
        min-height: 100dvh !important;
    }
    section[data-testid="stMain"] {
        min-height: 100dvh !important;
    }

    /* Collapse Streamlit's over-generous bottom padding down to the
       actual iOS safe-area value — keyboard-open state gets ~0 dead
       space, keyboard-closed state still respects the home indicator. */
    [data-testid="stBottomBlockContainer"] {
        padding-top: 0.5rem !important;
        padding-bottom: env(safe-area-inset-bottom, 0.5rem) !important;
    }
</style>
""", unsafe_allow_html=True)


# Auto-scroll to the newest chat message on every rerender.
# Streamlit doesn't include `<script>` in st.markdown output for
# security, so we use an invisible components.html iframe to inject JS
# that reaches into window.parent (the app) to scroll.
_scroll_snippet = """
<script>
    const scrollLatest = () => {
        try {
            const doc = window.parent.document;
            const msgs = doc.querySelectorAll('[data-testid="stChatMessage"]');
            if (msgs.length) {
                msgs[msgs.length - 1].scrollIntoView({behavior: 'smooth', block: 'end'});
            }
        } catch (e) {}
    };
    // Delay for streaming — the response fills in over ~2s
    setTimeout(scrollLatest, 100);
    setTimeout(scrollLatest, 800);
    setTimeout(scrollLatest, 2000);
</script>
"""


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
    render_brand_header(width_px=340)
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

# Language labels/names — derived once so we can reference them in the state
# reset block below and in the selectbox.
LANG_LABELS = [f"{flag}  {native}" for flag, native, _ in LANGUAGES]
LANG_NAMES = [name for _, _, name in LANGUAGES]
DEFAULT_LANG_LABEL = LANG_LABELS[0]  # English


# Reset chat when the game changes. Also reset the language-dropdown widget
# state so the picker snaps back to English for the new game.
if st.session_state.get("_active_game") != game_title:
    st.session_state._active_game = game_title
    st.session_state.messages = []
    st.session_state.language_pick = DEFAULT_LANG_LABEL


# Small brand mark above the switcher — anchors every chat view to the cafe.
render_brand_header(width_px=200)

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
    # Bind the selectbox to session_state via key= — this is the canonical
    # Streamlit pattern. Passing both `index=` and reading/writing the same
    # session_state key on every rerun fights the widget's own state and
    # silently reverts the user's selection.
    st.selectbox("Language", LANG_LABELS, key="language_pick")

# Derive the current language NAME from whatever the widget currently holds.
picked_label = st.session_state.get("language_pick", DEFAULT_LANG_LABEL)
current_language = LANG_NAMES[LANG_LABELS.index(picked_label)]


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
            # For follow-ups like "yes" or short clarifications, embed with
            # the prior user turn so retrieval sees what's actually being
            # asked. `session_state.messages[:-1]` excludes the current turn
            # we just appended.
            retrieval_query = build_retrieval_query(
                user_input, st.session_state.messages[:-1]
            )
            q_emb = voyage_client.embed(
                texts=[retrieval_query], model=VOYAGE_MODEL, input_type="query"
            ).embeddings[0]
            top = search_chunks(q_emb, chunks)
            prompt = build_rules_prompt(
                user_input, game_title, top,
                language=current_language,
            )
            source_pages = sorted({c["page"] for c in top})

        api_messages = build_message_history(st.session_state.messages, prompt)
        response_text = st.write_stream(
            anthropic_stream_with_retry(
                anthropic_client,
                model=CLAUDE_MODEL,
                max_tokens=2000,
                messages=api_messages,
            )
        )
        if source_pages:
            st.caption(f"Sources: pages {', '.join(str(p) for p in source_pages)}")

    st.session_state.messages.append({
        "role": "assistant",
        "content": response_text,
        "pages": source_pages,
    })


# --- Auto-scroll to newest message (only when the count grew) --------------
# Runs after any turn where the message count increased; avoids yanking the
# viewport around when the user is scrolled up reading old messages.
_msg_count = len(st.session_state.get("messages", []))
if _msg_count > st.session_state.get("_prev_msg_count", 0):
    st.session_state._prev_msg_count = _msg_count
    import streamlit.components.v1 as components
    components.html(_scroll_snippet, height=0)


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
