"""
Mobile-first browse-and-recommend experience for The Merry Meeple.

Two paths after picking party size:
  - Chip-driven: By Category / By Theme / By Mechanic -> chip picker -> filtered list
  - Recommend: 3 questions (playtime, complexity, themes) -> ranked list

No upfront family/audience filter — instead, individual games get Kids
or Adults pills on their cards.

Triggered by setting session_state.browse_mode = True and calling
run_browse_ui() in app.py.

Persists user choices to user_preferences and recommendation_log via
database_recs.
"""
import copy
import re

import streamlit as st

from recommender import (
    get_all_games, get_game_by_id, filter_games,
    top_cafe_categories, top_mechanics, top_themes,
    rank_games, popular_themes_for_user,
    is_kids_game, is_adults_game,
)
from database_recs import (
    save_preferences, log_recommendation, mark_recommendation_selected,
    create_or_get_user, init_recommendation_tables,
)


def _ensure_rec_user_id():
    """
    Bridge: the rules-assistant app uses `customer_phone` + `visit_id`,
    but the recommendation tables use a UUID `user_id`. Look up or
    create the user_id once per session and cache it in session_state.
    """
    if st.session_state.get("rec_user_id"):
        return st.session_state["rec_user_id"]
    phone = st.session_state.get("customer_phone")
    if not phone:
        return None
    if not st.session_state.get("rec_tables_initialized"):
        try:
            init_recommendation_tables()
        except UnicodeEncodeError:
            pass  # console encoding gripe; schema work succeeded
        st.session_state["rec_tables_initialized"] = True
    user = create_or_get_user(phone)
    if user:
        st.session_state["rec_user_id"] = user["user_id"]
        return user["user_id"]
    return None


# ---------------------------------------------------------------------------
# State + recommend-flow constants
# ---------------------------------------------------------------------------

DEFAULT_STATE = {
    "browse_step": "party_size",
    # Steps: party_size | hub
    #        | list                                 (chip-driven, search, recommend results)
    #        | rec_playtime | rec_complexity | rec_themes  (recommend path)
    #        | detail
    "browse_party_size": None,
    "browse_hub_path": None,                 # 'cafe' | 'mechanic' | 'theme' | 'recommend' | 'search'
    "browse_filters": [],                    # AND-stacked chip filters
    "browse_rec_playtime_max": None,         # int minutes or None
    "browse_rec_complexity_min": None,       # float
    "browse_rec_complexity_max": None,       # float
    "browse_rec_themes": [],                 # multi-select up to 3
    "browse_search_query": "",               # for the manual search path
    "browse_detail_game_id": None,
    "browse_show_more_chips": False,
    "browse_show_full_desc": False,
}


# Recommend-flow option lists (label, payload)
PLAYTIME_OPTIONS = [
    ("Quick (under 30 min)",   30),
    ("Medium (30–60 min)",     60),
    ("Long (60–120 min)",     120),
    ("All night (2+ hours)",  None),  # no cap
    ("No preference",         None),
]

COMPLEXITY_OPTIONS = [
    # (label, min_weight, max_weight)
    ("Light & easy to learn",  None, 2.0),
    ("Some strategy",          2.0,  3.0),
    ("Deep strategy",          3.0,  None),
    ("No preference",          None, None),
]

REC_THEMES_DEFAULT = 8        # show 8 chips before "+ More"
REC_THEMES_MAX_PICK = 3       # cap on multi-select


# ---------------------------------------------------------------------------
# State machine helpers
# ---------------------------------------------------------------------------

def _ensure_state():
    # deepcopy so mutable defaults (like browse_filters: []) aren't shared
    # across sessions.
    for k, v in DEFAULT_STATE.items():
        if k not in st.session_state:
            st.session_state[k] = copy.deepcopy(v)


_VALID_FILTER_KWARGS = {
    "cafe_category", "bgg_category", "mechanic", "theme", "themes_any",
    "playtime_max", "complexity_max", "complexity_min",
}


def _filters_apply(games, filters):
    """
    Apply every active filter (AND-stacked) to the candidate list.

    Defensive: skip filters whose `type` isn't a recognized filter_games
    kwarg. Stale session_state cookies from older builds can contain
    types we no longer support; we don't want a TypeError to crash
    the chip picker.
    """
    out = list(games)
    for f in filters:
        if not isinstance(f, dict):
            continue
        ftype = f.get("type")
        if ftype not in _VALID_FILTER_KWARGS:
            continue
        out = filter_games(out, **{ftype: f.get("value")})
    return out


def reset_browse_state():
    for k in list(DEFAULT_STATE.keys()):
        st.session_state.pop(k, None)
    st.session_state.pop("browse_mode", None)


def go_to_step(step):
    st.session_state.browse_step = step
    st.session_state.browse_show_more_chips = False
    st.rerun()


# Linear back order. List/detail back behaves contextually below.
_LINEAR_FLOW = ["party_size", "hub"]
_REC_FLOW = ["hub", "rec_playtime", "rec_complexity", "rec_themes", "list"]


def back_step():
    s = st.session_state.browse_step

    # Detail -> list
    if s == "detail":
        go_to_step("list")
        return

    # Recommend chain
    if s in _REC_FLOW:
        i = _REC_FLOW.index(s)
        if i > 0:
            # Going back from list (recommend mode) clears the latest answer
            if s == "list" and st.session_state.browse_hub_path == "recommend":
                st.session_state.browse_rec_themes = []
                go_to_step("rec_themes")
                return
            if s == "rec_themes":
                st.session_state.browse_rec_themes = []
            elif s == "rec_complexity":
                st.session_state.browse_rec_complexity_min = None
                st.session_state.browse_rec_complexity_max = None
            elif s == "rec_playtime":
                st.session_state.browse_rec_playtime_max = None
                # leaving recommend entirely
                st.session_state.browse_hub_path = None
            go_to_step(_REC_FLOW[i - 1])
            return

    # Chip-driven list — drop the most recent chip filter
    if s == "list":
        if st.session_state.browse_filters:
            st.session_state.browse_filters.pop()
        st.session_state.browse_hub_path = None
        go_to_step("hub")
        return

    # Linear: party_size <-> hub
    if s in _LINEAR_FLOW:
        i = _LINEAR_FLOW.index(s)
        if i > 0:
            go_to_step(_LINEAR_FLOW[i - 1])
        else:
            reset_browse_state()
            st.rerun()
        return


# ---------------------------------------------------------------------------
# Mobile styling
# ---------------------------------------------------------------------------

MOBILE_CSS = """
<style>
.browse-title { font-size: 1.4rem; font-weight: 600; margin: 0.2rem 0 0.6rem 0; }
.browse-step  { font-size: 0.85rem; opacity: 0.6; margin-bottom: 0.5rem; }
.browse-help  { font-size: 0.95rem; opacity: 0.75; margin-bottom: 1rem; }
.game-pill    {
  display: inline-block; padding: 2px 10px; margin: 2px;
  border-radius: 10px;
  background: rgba(128,128,128,0.18);
  color: inherit;
  font-size: 0.8rem;
}
.game-pill-kids   { background: rgba(80, 200, 120, 0.30); }
.game-pill-adults { background: rgba(200, 70, 90, 0.30); }
.game-rank-badge {
  display: inline-block; padding: 1px 8px; border-radius: 8px;
  background: rgba(120, 90, 180, 0.5);
  color: inherit;
  font-size: 0.75rem; font-weight: 600;
}
</style>
"""


def _strip_html(s, limit=None):
    if not s:
        return ""
    s = (s.replace("&mdash;", "—").replace("&ndash;", "–")
           .replace("&quot;", '"').replace("&#10;", " ")
           .replace("&amp;", "&").replace("&nbsp;", " "))
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return (s[:limit] + "...") if limit and len(s) > limit else s


def _display_theme(t):
    """'Theme: Nature' -> 'Nature', 'Animals: Birds' -> 'Birds'."""
    if ":" in t:
        return t.split(":", 1)[1].strip()
    return t


def _format_player_ranges(ranges):
    if not ranges:
        return ""
    parts = []
    for r in ranges:
        if isinstance(r, dict):
            lo, hi = r.get("min"), r.get("max")
            if lo == hi or hi is None:
                parts.append(str(lo))
            elif lo is None:
                parts.append(str(hi))
            else:
                parts.append(f"{lo}–{hi}")
    return ", ".join(parts)


def _audience_pills_html(game):
    """HTML for Kids / Adults pills (used in detail view, where layout
    can mix HTML and Streamlit elements freely)."""
    parts = []
    if is_kids_game(game):
        parts.append("<span class='game-pill game-pill-kids'>👶 Kids</span>")
    if is_adults_game(game):
        parts.append("<span class='game-pill game-pill-adults'>🍷 Adults</span>")
    return " ".join(parts)


def _audience_inline_label(game):
    """
    Inline-text audience tag for embedding inside a Streamlit button label
    (which can't render styled HTML). Empty string when game is neither
    explicitly kids nor adults.
    """
    tags = []
    if is_kids_game(game):
        tags.append("👶 Kids")
    if is_adults_game(game):
        tags.append("🍷 Adults")
    return "  ·  ".join(tags)


# ---------------------------------------------------------------------------
# Step 1 — party size
# ---------------------------------------------------------------------------

def _render_party_size():
    st.markdown("<div class='browse-step'>Step 1 of 2</div>", unsafe_allow_html=True)
    st.markdown("<div class='browse-title'>How many players?</div>",
                unsafe_allow_html=True)
    st.markdown("<div class='browse-help'>Tap your party size to start.</div>",
                unsafe_allow_html=True)

    if st.button("1 (solo)", key="party_1", use_container_width=True):
        st.session_state.browse_party_size = 1
        _save_pref("party_size", "1")
        go_to_step("hub")

    options = [("2", 2), ("3", 3), ("4", 4), ("5", 5), ("6+", 6)]
    cols = st.columns(len(options))
    for col, (label, val) in zip(cols, options):
        with col:
            if st.button(label, key=f"party_{val}", use_container_width=True):
                st.session_state.browse_party_size = val
                _save_pref("party_size", str(val))
                go_to_step("hub")


# ---------------------------------------------------------------------------
# Step 2 — hub
# ---------------------------------------------------------------------------

def _render_hub():
    st.markdown("<div class='browse-step'>Step 2 of 2</div>", unsafe_allow_html=True)
    st.markdown("<div class='browse-title'>How do you want to browse?</div>",
                unsafe_allow_html=True)
    st.markdown(
        f"<div class='browse-help'>{st.session_state.browse_party_size} players</div>",
        unsafe_allow_html=True,
    )

    if st.button("🎯 Recommend me something",
                 key="hub_recommend", use_container_width=True, type="primary"):
        st.session_state.browse_hub_path = "recommend"
        go_to_step("rec_playtime")

    if st.button("🏷️ By Category — Strategy, Party, Family, Kids…",
                 key="hub_cafe", use_container_width=True):
        st.session_state.browse_hub_path = "cafe"
        go_to_step("list")
    if st.button("🎭 By Theme — Fantasy, Sci-Fi, Animals…",
                 key="hub_theme", use_container_width=True):
        st.session_state.browse_hub_path = "theme"
        go_to_step("list")
    if st.button("⚙️ By Mechanic — Drafting, Worker Placement…",
                 key="hub_mech", use_container_width=True):
        st.session_state.browse_hub_path = "mechanic"
        go_to_step("list")
    if st.button("🔍 Search for a game by name",
                 key="hub_search", use_container_width=True):
        st.session_state.browse_hub_path = "search"
        st.session_state.browse_search_query = ""
        go_to_step("list")


# ---------------------------------------------------------------------------
# Recommend flow — 3 questions
# ---------------------------------------------------------------------------

def _render_rec_playtime():
    st.markdown("<div class='browse-step'>Recommend · Step 1 of 3</div>",
                unsafe_allow_html=True)
    st.markdown("<div class='browse-title'>How long do you want to play?</div>",
                unsafe_allow_html=True)
    for label, max_min in PLAYTIME_OPTIONS:
        if st.button(label, key=f"rec_pt_{label}", use_container_width=True):
            st.session_state.browse_rec_playtime_max = max_min
            _save_pref("rec_playtime_max", str(max_min) if max_min else "any")
            go_to_step("rec_complexity")


def _render_rec_complexity():
    st.markdown("<div class='browse-step'>Recommend · Step 2 of 3</div>",
                unsafe_allow_html=True)
    st.markdown("<div class='browse-title'>How complex do you want it?</div>",
                unsafe_allow_html=True)
    for label, lo, hi in COMPLEXITY_OPTIONS:
        if st.button(label, key=f"rec_cx_{label}", use_container_width=True):
            st.session_state.browse_rec_complexity_min = lo
            st.session_state.browse_rec_complexity_max = hi
            _save_pref("rec_complexity",
                       f"{lo or 'any'}-{hi or 'any'}")
            go_to_step("rec_themes")


def _render_rec_themes():
    st.markdown("<div class='browse-step'>Recommend · Step 3 of 3</div>",
                unsafe_allow_html=True)
    st.markdown("<div class='browse-title'>What themes interest you?</div>",
                unsafe_allow_html=True)
    st.markdown(
        f"<div class='browse-help'>Pick up to {REC_THEMES_MAX_PICK}, "
        f"or skip for any theme.</div>",
        unsafe_allow_html=True,
    )

    selected = list(st.session_state.browse_rec_themes)

    # Re-derive the candidate themes against games that already pass
    # party_size + the user's playtime/complexity choices so empty-result
    # themes don't appear as options.
    all_games = get_all_games()
    base = filter_games(
        all_games,
        party_size=st.session_state.browse_party_size,
        playtime_max=st.session_state.browse_rec_playtime_max,
        complexity_min=st.session_state.browse_rec_complexity_min,
        complexity_max=st.session_state.browse_rec_complexity_max,
    )
    show_more = st.session_state.browse_show_more_chips
    chips_full = top_themes(base, limit=24)
    chips = chips_full if show_more else chips_full[:REC_THEMES_DEFAULT]

    if not chips_full:
        st.markdown("<div class='browse-help'>No themes available for the "
                     "filters you've picked. Tap continue to see all matching "
                     "games.</div>", unsafe_allow_html=True)
    else:
        for c in chips:
            label = _display_theme(c)
            if c in selected:
                label = f"✓ {label}"
            disabled = (len(selected) >= REC_THEMES_MAX_PICK
                        and c not in selected)
            if st.button(label, key=f"rec_th_{c}",
                          use_container_width=True, disabled=disabled):
                if c in selected:
                    selected.remove(c)
                elif len(selected) < REC_THEMES_MAX_PICK:
                    selected.append(c)
                st.session_state.browse_rec_themes = selected
                st.rerun()
        if not show_more and len(chips_full) > REC_THEMES_DEFAULT:
            if st.button(f"+ {len(chips_full) - REC_THEMES_DEFAULT} more",
                         key="rec_themes_more", use_container_width=True):
                st.session_state.browse_show_more_chips = True
                st.rerun()

    label = "Skip themes" if not selected else f"Continue ({len(selected)} picked)"
    if st.button(label, key="rec_themes_continue",
                 use_container_width=True, type="primary"):
        if selected:
            _save_pref("rec_themes", ", ".join(selected))
        go_to_step("list")


# ---------------------------------------------------------------------------
# Step 3 — list (chip-driven OR recommend-driven)
# ---------------------------------------------------------------------------

def _render_list():
    path = st.session_state.browse_hub_path
    party = st.session_state.browse_party_size
    all_games = get_all_games()
    base = filter_games(all_games, party_size=party)

    if path == "recommend":
        _render_recommend_list(all_games, base, party)
        return

    if path == "search":
        _render_search(base)
        return

    # Chip-driven paths: cafe / theme / mechanic
    active = st.session_state.browse_filters
    if path in ("cafe", "theme", "mechanic"):
        _render_chip_picker(path, base, active)
        return

    # No path — render filtered results from active chip filters
    results = _filters_apply(base, active)
    user_id = st.session_state.get("rec_user_id")
    user_themes = popular_themes_for_user(user_id) if user_id else None
    rank_games(results, party_size=party, user_themes=user_themes)

    _render_active_filter_pills(active)
    if st.button("+ Add another filter", key="add_filter",
                 use_container_width=True):
        st.session_state.browse_show_more_chips = False
        go_to_step("hub")
    st.markdown(
        f"<div class='browse-help'>{len(results)} games match.</div>",
        unsafe_allow_html=True,
    )
    _render_game_list(results[:30])


def _render_chip_picker(path, base, active):
    type_key = {"cafe": "cafe_category",
                "theme": "theme",
                "mechanic": "mechanic"}[path]
    already_picked = {f["value"] for f in active if f["type"] == type_key}

    candidates_after_active = _filters_apply(base, active)
    label, chips_full = {
        "cafe":     ("Pick a category",
                      top_cafe_categories(candidates_after_active, limit=20)),
        "theme":    ("Pick a theme",
                      top_themes(candidates_after_active, limit=20)),
        "mechanic": ("Pick a mechanic",
                      top_mechanics(candidates_after_active, limit=20)),
    }[path]
    chips_full = [c for c in chips_full if c not in already_picked]

    show_more = st.session_state.browse_show_more_chips
    chips = chips_full if show_more else chips_full[:8]

    if active:
        _render_active_filter_pills(active)

    st.markdown(f"<div class='browse-title'>{label}</div>",
                unsafe_allow_html=True)
    if not chips:
        st.markdown(
            "<div class='browse-help'>No options left under the current filters. "
            "Remove one above to widen the list.</div>",
            unsafe_allow_html=True,
        )
        return

    for c in chips:
        chip_label = _display_theme(c) if path == "theme" else c
        if st.button(chip_label, key=f"chip_{path}_{c}",
                     use_container_width=True):
            st.session_state.browse_filters.append(
                {"type": type_key, "value": c}
            )
            _save_pref(path, c)
            st.session_state.browse_show_more_chips = False
            st.session_state.browse_hub_path = None
            st.rerun()
    if not show_more and len(chips_full) > 8:
        if st.button(f"+ {len(chips_full) - 8} more",
                     key="more_chips", use_container_width=True):
            st.session_state.browse_show_more_chips = True
            st.rerun()


def _render_active_filter_pills(active):
    if not active:
        return
    st.markdown("<div class='browse-help'>Filters:</div>",
                unsafe_allow_html=True)
    cols = st.columns(min(len(active), 3))
    for i, f in enumerate(active):
        with cols[i % len(cols)]:
            label = f"✕ {_filter_pretty(f)}"
            if st.button(label, key=f"rm_filter_{i}",
                         use_container_width=True):
                st.session_state.browse_filters = [
                    g for j, g in enumerate(active) if j != i
                ]
                st.rerun()


def _filter_pretty(f):
    label = {
        "cafe_category": "Category",
        "theme":         "Theme",
        "mechanic":      "Mechanic",
    }.get(f["type"], f["type"])
    val = f["value"]
    if f["type"] == "theme":
        val = _display_theme(val)
    return f"{label}: {val}"


def _render_recommend_list(all_games, base, party):
    """Apply recommend hard filters, soft-rank by selected themes, render list."""
    pmax = st.session_state.browse_rec_playtime_max
    cmin = st.session_state.browse_rec_complexity_min
    cmax = st.session_state.browse_rec_complexity_max
    themes = st.session_state.browse_rec_themes or []

    results = filter_games(
        base,
        playtime_max=pmax,
        complexity_min=cmin,
        complexity_max=cmax,
    )
    user_id = st.session_state.get("rec_user_id")
    user_themes = popular_themes_for_user(user_id) if user_id else None
    rank_games(results, party_size=party,
               user_themes=user_themes,
               selected_themes=themes if themes else None)

    # Render the criteria as read-only context (back-arrow to change)
    crit = []
    crit.append(f"{party} players")
    if pmax:
        crit.append(f"≤ {pmax} min")
    elif pmax is None and st.session_state.browse_step == "list":
        pass  # "no preference" or "all night" both leave pmax None
    if cmin or cmax:
        if cmax and not cmin:
            crit.append(f"weight ≤ {cmax:.1f}")
        elif cmin and not cmax:
            crit.append(f"weight ≥ {cmin:.1f}")
        elif cmin and cmax:
            crit.append(f"weight {cmin:.1f}–{cmax:.1f}")
    if themes:
        crit.append(", ".join(_display_theme(t) for t in themes))
    summary = " · ".join(crit)
    st.markdown(f"<div class='browse-help'>Recommending: {summary}</div>",
                unsafe_allow_html=True)
    st.markdown(f"<div class='browse-help'>{len(results)} games match.</div>",
                unsafe_allow_html=True)
    _render_game_list(results[:30])


# ---------------------------------------------------------------------------
# Search-by-name path
# ---------------------------------------------------------------------------

def _normalize_for_search(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _search_games(query, games):
    """
    Rank games by how well their name matches `query`.
    Order: exact normalized match > startswith > substring > word-token match.
    Empty query returns all games sorted alphabetically.
    """
    if not query.strip():
        return sorted(games, key=lambda g: g["name"].lower())
    q = _normalize_for_search(query)
    if not q:
        return sorted(games, key=lambda g: g["name"].lower())

    exact, prefix, substring, token = [], [], [], []
    q_words = set(re.findall(r"[a-z0-9]+", query.lower()))
    for g in games:
        n = _normalize_for_search(g["name"])
        if n == q:
            exact.append(g)
        elif n.startswith(q):
            prefix.append(g)
        elif q in n:
            substring.append(g)
        else:
            name_words = set(re.findall(r"[a-z0-9]+", g["name"].lower()))
            if q_words & name_words:
                token.append(g)
    # Within each bucket, alphabetical
    for bucket in (exact, prefix, substring, token):
        bucket.sort(key=lambda g: g["name"].lower())
    return exact + prefix + substring + token


def _render_search(base):
    st.markdown("<div class='browse-step'>Search</div>", unsafe_allow_html=True)
    st.markdown("<div class='browse-title'>Search for a game</div>",
                unsafe_allow_html=True)

    # st_keyup reruns the script on every keypress (with a small debounce),
    # so the result list filters as the user types — vanilla
    # st.text_input only reruns on Enter/blur.
    from st_keyup import st_keyup
    query = st_keyup(
        "Game name", key="browse_search_query",
        placeholder="Catan, Wingspan, Codenames…",
        label_visibility="collapsed",
        debounce=200,
    )

    matches = _search_games(query or "", base)

    if query:
        st.markdown(
            f"<div class='browse-help'>{len(matches)} game(s) match.</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"<div class='browse-help'>Showing all {len(matches)} games "
            f"that fit your party. Start typing to filter.</div>",
            unsafe_allow_html=True,
        )

    _render_game_list(matches[:50])


# ---------------------------------------------------------------------------
# Game list + detail
# ---------------------------------------------------------------------------

def _render_game_list(games):
    for g in games:
        complexity = g.get("complexity") or 0
        rating = g.get("avg_rating") or 0
        pieces = []
        if g.get("min_players") and g.get("max_players"):
            pieces.append(f"👥 {g['min_players']}–{g['max_players']}")
        elif g.get("min_players"):
            pieces.append(f"👥 {g['min_players']}+")
        playtime = g.get("playtime") or g.get("max_playtime")
        if playtime:
            pieces.append(f"🕐 {playtime}m")
        if complexity:
            pieces.append(f"🧠 {complexity:.1f}")
        if rating:
            pieces.append(f"⭐ {rating:.1f}")
        meta = "  ·  ".join(pieces)

        # Audience tag embedded in the button label so it stays inside
        # the card (Streamlit buttons can't render styled HTML pills).
        audience = _audience_inline_label(g)
        label = f"**{g['name']}**\n\n{meta}"
        if audience:
            label += f"\n\n{audience}"
        if st.button(label, key=f"game_{g['game_id']}",
                     use_container_width=True):
            st.session_state.browse_detail_game_id = g["game_id"]
            st.session_state.browse_show_full_desc = False
            go_to_step("detail")


def _render_detail():
    game_id = st.session_state.browse_detail_game_id
    g = get_game_by_id(game_id)
    if not g:
        st.error("Game not found.")
        if st.button("← Back", key="detail_back_err"):
            back_step()
        return

    short = g.get("short_description") or _strip_html(g.get("description") or "", 220)

    st.markdown(f"<div class='browse-title'>{g['name']}</div>",
                unsafe_allow_html=True)
    if g.get("image_url"):
        st.image(g["image_url"], use_container_width=True)

    audience_html = _audience_pills_html(g)
    if audience_html:
        st.markdown(audience_html, unsafe_allow_html=True)

    pills = []
    if g.get("min_players") and g.get("max_players"):
        pills.append(f"👥 {g['min_players']}–{g['max_players']}")
    if g.get("playtime"):
        pills.append(f"🕐 {g['playtime']} min")
    if g.get("complexity"):
        pills.append(f"🧠 {g['complexity']:.1f}/5")
    if g.get("avg_rating"):
        pills.append(f"⭐ {g['avg_rating']:.1f}")
    if g.get("community_player_age"):
        pills.append(f"🎂 {g['community_player_age']}")
    if pills:
        st.markdown(
            " &nbsp; ".join(f"<span class='game-pill'>{p}</span>" for p in pills),
            unsafe_allow_html=True,
        )

    if g.get("best_player_count"):
        best = _format_player_ranges(g["best_player_count"])
        if best:
            st.caption(f"Best with {best} players")

    if short:
        st.markdown(f"<div style='margin-top:0.6rem'>{short}</div>",
                    unsafe_allow_html=True)
    full = _strip_html(g.get("description") or "")
    if full and len(full) > len(short or "") + 50:
        if st.session_state.browse_show_full_desc:
            with st.expander("Less", expanded=True):
                st.write(full)
        else:
            if st.button("Tell me more", key="more_desc",
                         use_container_width=True):
                st.session_state.browse_show_full_desc = True
                st.rerun()

    st.markdown("&nbsp;", unsafe_allow_html=True)
    if st.button(f"🎯 Pick {g['name']}", key="pick_game",
                 use_container_width=True, type="primary"):
        _pick_game(g)
        st.success(f"Staff notified — bringing {g['name']} to your table.")

    if g.get("bgg_url"):
        st.markdown(
            f"<a href='{g['bgg_url']}' target='_blank' "
            f"style='display:block; text-align:center; margin-top:0.4rem;'>"
            f"View on BoardGameGeek ↗</a>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _save_pref(pref_type, value):
    user_id = _ensure_rec_user_id()
    visit_id = st.session_state.get("visit_id")
    if not user_id:
        return
    save_preferences(user_id, visit_id, [{
        "type": pref_type, "value": str(value), "confidence": 1.0,
    }])


def _pick_game(game):
    user_id = _ensure_rec_user_id()
    visit_id = st.session_state.get("visit_id")
    if user_id and visit_id:
        log_recommendation(
            session_id=visit_id, user_id=user_id, game_id=game["game_id"],
            score=1.0, breakdown={"reason": "browse_pick"},
            version="browse-v2", position=1,
        )
        mark_recommendation_selected(visit_id, game["game_id"])
    st.session_state._pending_ping = {
        "idx": None, "reason": "new_game",
        "extra": {"game_name": game["name"]},
    }
    st.session_state._ping_dialog_opened = False


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def run_browse_ui():
    _ensure_state()
    st.markdown(MOBILE_CSS, unsafe_allow_html=True)

    cols = st.columns([1, 5, 1])
    with cols[0]:
        if st.button("←", key="browse_back", help="Back"):
            back_step()
    with cols[2]:
        if st.button("✕", key="browse_exit", help="Exit to chat"):
            reset_browse_state()
            st.rerun()

    step = st.session_state.browse_step
    if step == "party_size":
        _render_party_size()
    elif step == "hub":
        _render_hub()
    elif step == "rec_playtime":
        _render_rec_playtime()
    elif step == "rec_complexity":
        _render_rec_complexity()
    elif step == "rec_themes":
        _render_rec_themes()
    elif step == "list":
        _render_list()
    elif step == "detail":
        _render_detail()
    else:
        st.warning(f"Unknown step: {step}")
        if st.button("Reset"):
            reset_browse_state()
            st.rerun()
