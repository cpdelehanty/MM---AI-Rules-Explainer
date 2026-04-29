"""
Mobile-first browse-and-recommend experience for The Merry Meeple.

Renders a multi-step click-through (party size -> family filter -> hub ->
filtered list -> game detail) inside Streamlit. Triggered by setting
session_state.browse_mode = True and calling run_browse_ui() in app.py.

Persists user choices to user_preferences and recommendation_log via
database_recs.
"""

import streamlit as st

from recommender import (
    get_all_games, get_game_by_id, filter_games, find_similar,
    top_cafe_categories, top_bgg_categories, top_mechanics, top_themes,
    fuzzy_find_anchor, rank_games, popular_themes_for_user,
    parse_freeform_query, relax_filters_to_min, FAMILY_FILTERS,
)
from database_recs import (
    save_preferences, log_recommendation, mark_recommendation_selected,
    create_or_get_user, init_recommendation_tables,
)


def _ensure_rec_user_id():
    """
    Bridge: the rules-assistant app uses `customer_phone` + `visit_id`,
    but the recommendation tables use a UUID `user_id`. Look up or create
    the user_id once per session and cache it in session_state.
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
            # The init function prints a unicode emoji which can fail on
            # cp1252 consoles. The schema work itself succeeded.
            pass
        st.session_state["rec_tables_initialized"] = True
    user = create_or_get_user(phone)
    if user:
        st.session_state["rec_user_id"] = user["user_id"]
        return user["user_id"]
    return None


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

DEFAULT_STATE = {
    "browse_step": "party_size",      # party_size | family | hub | list | detail
    "browse_party_size": None,
    "browse_family": None,             # 'family' | 'all' | 'adult'
    "browse_hub_path": None,           # 'cafe' | 'mechanic' | 'theme' | 'like'
    "browse_filters": [],              # list of {"type": ..., "value": ...} — AND-stacked
    "browse_anchor_name": None,         # for "like X" path
    "browse_detail_game_id": None,
    "browse_show_more_chips": False,
    "browse_show_full_desc": False,
    "browse_summary": None,             # Claude's interpretation of the freeform query
}


def _filters_apply(games, filters):
    """Apply every active filter (AND-stacked) to the candidate list."""
    out = list(games)
    for f in filters:
        out = filter_games(out, **{f["type"]: f["value"]})
    return out


def _filter_pretty(f):
    """Display label for an active filter chip."""
    if f["type"] == "playtime_max":
        return f"Under {f['value']} min"
    label = {
        "cafe_category": "Category",
        "bgg_category":  "Category",
        "theme":         "Theme",
        "mechanic":      "Mechanic",
    }.get(f["type"], f["type"])
    val = f["value"]
    if f["type"] == "theme":
        val = _display_theme(val)
    return f"{label}: {val}"


def _ensure_state():
    for k, v in DEFAULT_STATE.items():
        if k not in st.session_state:
            st.session_state[k] = v


def reset_browse_state():
    for k in list(DEFAULT_STATE.keys()):
        st.session_state.pop(k, None)
    st.session_state.pop("browse_mode", None)


def go_to_step(step):
    st.session_state.browse_step = step
    st.session_state.browse_show_more_chips = False
    st.rerun()


def back_step():
    """
    Smart back: detail -> list -> hub -> family -> party_size.

    When stepping back from the filtered list to the hub, we drop the most
    recent filter so the user can re-pick. This matches "back = undo my
    last action". To preserve filters and add another, use the explicit
    "+ Add another filter" button on the list.
    """
    s = st.session_state.browse_step
    flow = ["party_size", "family", "hub", "list", "detail"]
    if s in flow:
        i = flow.index(s)
        if i > 0:
            # Going from list back to hub: pop the most recent filter
            if s == "list":
                if st.session_state.browse_filters:
                    st.session_state.browse_filters.pop()
                # Also clear any in-progress chip selection / NL summary
                st.session_state.browse_hub_path = None
                st.session_state.browse_anchor_name = None
                st.session_state.browse_summary = None
            go_to_step(flow[i - 1])
        else:
            reset_browse_state()
            st.rerun()


# ---------------------------------------------------------------------------
# Mobile styling helpers
# ---------------------------------------------------------------------------

MOBILE_CSS = """
<style>
.browse-title { font-size: 1.4rem; font-weight: 600; margin: 0.2rem 0 0.6rem 0; }
/* Theme-aware muted colors that work in light and dark mode */
.browse-step  { font-size: 0.85rem; opacity: 0.6; margin-bottom: 0.5rem; }
.browse-help  { font-size: 0.95rem; opacity: 0.75; margin-bottom: 1rem; }
.game-pill    {
  display: inline-block; padding: 2px 10px; margin: 2px;
  border-radius: 10px;
  background: rgba(128,128,128,0.18);
  color: inherit;
  font-size: 0.8rem;
}
.game-rank-badge {
  display: inline-block; padding: 1px 8px; border-radius: 8px;
  background: rgba(120, 90, 180, 0.5);
  color: inherit;
  font-size: 0.75rem; font-weight: 600;
}
</style>
"""


def _strip_html(s, limit=None):
    import re
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


# ---------------------------------------------------------------------------
# Step 1: Party size
# ---------------------------------------------------------------------------

def _render_party_size():
    st.markdown("<div class='browse-step'>Step 1 of 4</div>", unsafe_allow_html=True)
    st.markdown("<div class='browse-title'>How many players?</div>", unsafe_allow_html=True)
    st.markdown("<div class='browse-help'>Tap your party size to start.</div>",
                unsafe_allow_html=True)

    # Solo gets its own row, then 2-6+ in a row of five — keeps the solo
    # affordance from getting squished and keeps tap targets large on mobile.
    if st.button("1 (solo)", key="party_1", use_container_width=True):
        st.session_state.browse_party_size = 1
        _save_pref("party_size", "1")
        go_to_step("family")

    options = [("2", 2), ("3", 3), ("4", 4), ("5", 5), ("6+", 6)]
    cols = st.columns(len(options))
    for col, (label, val) in zip(cols, options):
        with col:
            if st.button(label, key=f"party_{val}", use_container_width=True):
                st.session_state.browse_party_size = val
                _save_pref("party_size", str(val))
                go_to_step("family")


# ---------------------------------------------------------------------------
# Step 2: Family filter
# ---------------------------------------------------------------------------

def _render_family():
    st.markdown("<div class='browse-step'>Step 2 of 4</div>", unsafe_allow_html=True)
    st.markdown("<div class='browse-title'>Audience?</div>", unsafe_allow_html=True)
    st.markdown(
        f"<div class='browse-help'>Party of {st.session_state.browse_party_size}. "
        f"Pick what fits the group.</div>",
        unsafe_allow_html=True,
    )

    # Vertical stacking — full width for big tap targets
    if st.button("👨‍👩‍👧 Family-friendly",
                 key="fam_family", use_container_width=True,
                 help="Recommended ages 8 and under welcome"):
        st.session_state.browse_family = "family"
        _save_pref("family_filter", "family")
        go_to_step("hub")
    if st.button("🎲 All audiences",
                 key="fam_all", use_container_width=True,
                 help="Mixed group; no age filter"):
        st.session_state.browse_family = "all"
        _save_pref("family_filter", "all")
        go_to_step("hub")
    if st.button("🍷 Adults only",
                 key="fam_adult", use_container_width=True,
                 help="Edgier titles, ages 14+"):
        st.session_state.browse_family = "adult"
        _save_pref("family_filter", "adult")
        go_to_step("hub")


# ---------------------------------------------------------------------------
# Step 3: Hub — four browse paths
# ---------------------------------------------------------------------------

def _render_hub():
    st.markdown("<div class='browse-step'>Step 3 of 4</div>", unsafe_allow_html=True)
    st.markdown("<div class='browse-title'>How do you want to browse?</div>",
                unsafe_allow_html=True)

    fam_label = FAMILY_FILTERS[st.session_state.browse_family]["label"]
    st.markdown(
        f"<div class='browse-help'>"
        f"{st.session_state.browse_party_size} players · {fam_label}"
        f"</div>",
        unsafe_allow_html=True,
    )

    if st.button("🏷️ By Category — Strategy, Party, Family…",
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
    if st.button("✨ Tell me what you want…",
                 key="hub_like", use_container_width=True):
        st.session_state.browse_hub_path = "like"
        go_to_step("list")


# ---------------------------------------------------------------------------
# Step 4: Filtered list / chip picker
# ---------------------------------------------------------------------------

def _render_list():
    path = st.session_state.browse_hub_path
    party = st.session_state.browse_party_size
    family = st.session_state.browse_family
    all_games = get_all_games()
    base_filtered = filter_games(all_games, party_size=party, family_filter=family)

    st.markdown("<div class='browse-step'>Step 4 of 4</div>", unsafe_allow_html=True)

    if path == "like":
        _render_like_path(all_games, party, family)
        return

    # If hub_path is set, the user just arrived from the hub and is picking
    # a chip for that path. After they pick (or back out), hub_path is
    # cleared and we render the filtered list.
    active = st.session_state.browse_filters
    if path in ("cafe", "theme", "mechanic"):
        _render_chip_picker(path, base_filtered, active)
        return

    # All filters picked — render the result list with active-filter pills
    # at the top so users can remove any filter.
    results = _filters_apply(base_filtered, active)

    user_id = st.session_state.get("rec_user_id")
    user_themes = popular_themes_for_user(user_id) if user_id else None
    rank_games(results, party_size=party, user_themes=user_themes)

    summary = st.session_state.get("browse_summary")
    if summary:
        st.markdown(
            f"<div class='browse-help'><em>Reading you as:</em> {summary}</div>",
            unsafe_allow_html=True,
        )
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


def _render_chip_picker(path, base_filtered, active):
    """Render the chip grid for the current browse path."""
    # Only show chips that still match — exclude any that produce zero results
    # given the already-active filters.
    candidates_after_active = _filters_apply(base_filtered, active)

    label, chips_full = {
        "cafe":     ("Pick a category",
                      top_cafe_categories(candidates_after_active, limit=20)),
        "theme":    ("Pick a theme",
                      top_themes(candidates_after_active, limit=20)),
        "mechanic": ("Pick a mechanic",
                      top_mechanics(candidates_after_active, limit=20)),
    }[path]

    # Hide chip values already in the active filter list for this path's type
    type_key = {"cafe": "cafe_category", "theme": "theme", "mechanic": "mechanic"}[path]
    already_picked = {f["value"] for f in active if f["type"] == type_key}
    chips_full = [c for c in chips_full if c not in already_picked]

    show_more = st.session_state.browse_show_more_chips
    chips = chips_full if show_more else chips_full[:8]

    if active:
        _render_active_filter_pills(active)

    st.markdown(f"<div class='browse-title'>{label}</div>", unsafe_allow_html=True)
    if not chips:
        st.markdown(
            "<div class='browse-help'>No options left under the current filters. "
            "Remove one above to widen the list.</div>",
            unsafe_allow_html=True,
        )
        return

    type_key = {"cafe": "cafe_category", "theme": "theme", "mechanic": "mechanic"}[path]
    for c in chips:
        label_ = _display_theme(c) if path == "theme" else c
        if st.button(label_, key=f"chip_{path}_{c}", use_container_width=True):
            st.session_state.browse_filters.append({"type": type_key, "value": c})
            _save_pref(path, c)
            st.session_state.browse_show_more_chips = False
            st.session_state.browse_hub_path = None  # exit chip-picking mode
            st.rerun()
    if not show_more and len(chips_full) > 8:
        if st.button(f"+ {len(chips_full) - 8} more",
                     key="more_chips", use_container_width=True):
            st.session_state.browse_show_more_chips = True
            st.rerun()


def _render_active_filter_pills(active):
    """Render removable pills for each active filter."""
    if not active:
        return
    st.markdown("<div class='browse-help'>Filters:</div>", unsafe_allow_html=True)
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


def _get_anthropic_client():
    """Cache an Anthropic client in session_state so we don't rebuild it each render."""
    import os
    if "anthropic_client" not in st.session_state:
        from anthropic import Anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None
        st.session_state["anthropic_client"] = Anthropic(api_key=api_key)
    return st.session_state["anthropic_client"]


def _handle_freeform_query(query, all_games):
    """
    Route the user's text input. If it looks like a game name we have,
    set it as the anchor. Otherwise, parse with Claude into filters.
    """
    # Step 1: fuzzy match against game names
    hits = fuzzy_find_anchor(query, all_games=all_games)
    exact = [g for g in hits if g["name"].lower() == query.lower().strip()]
    if exact:
        st.session_state.browse_anchor_name = exact[0]["name"]
        _save_pref("like_anchor", exact[0]["name"])
        st.rerun()
        return

    # Step 2: Claude parse for natural-language descriptions
    client = _get_anthropic_client()
    if client is None:
        st.error("Natural-language search needs an ANTHROPIC_API_KEY.")
        return

    with st.spinner("Finding the right vibe…"):
        try:
            parsed = parse_freeform_query(query, client, all_games=all_games)
        except Exception as e:
            st.error(f"Couldn't parse that — try rewording? ({e})")
            return

    if not parsed["filters"] and not parsed["anchors"]:
        # Last-resort: fall back to fuzzy hits if Claude returned nothing useful
        if hits:
            st.markdown("<div class='browse-help'>Best guesses:</div>",
                         unsafe_allow_html=True)
            for g in hits[:5]:
                if st.button(g["name"], key=f"fb_anchor_{g['game_id']}",
                             use_container_width=True):
                    st.session_state.browse_anchor_name = g["name"]
                    _save_pref("like_anchor", g["name"])
                    st.rerun()
            return
        st.markdown(
            "<div class='browse-help'>Couldn't pin that down. "
            "Try naming a game you love, or a mechanic/theme like "
            "'cooperative dungeon crawler'.</div>",
            unsafe_allow_html=True,
        )
        return

    # Apply filters with relaxation. Ensure the user sees at least 3 games:
    #   - If filters relax cleanly to 3+, show the filtered list.
    #   - If all filters were dropped and we have an anchor, switch to anchor mode.
    #   - Else (no filters, no anchor) leave filters empty so the list shows
    #     all games that match party_size + family — never fewer than 3.
    summary = parsed["summary"] or ""
    party = st.session_state.browse_party_size
    family = st.session_state.browse_family

    if parsed["filters"]:
        kept, dropped, _ = relax_filters_to_min(
            parsed["filters"], all_games,
            party_size=party, family_filter=family, min_results=3,
        )
        st.session_state.browse_hub_path = None
        if dropped and not kept and parsed["anchors"]:
            # Filters all dropped — fall back to anchor similarity
            st.session_state.browse_filters = []
            st.session_state.browse_anchor_name = parsed["anchors"][0]
            summary += (f" (no precise matches — showing games similar to "
                        f"{parsed['anchors'][0]})")
        elif dropped and not kept:
            st.session_state.browse_filters = []
            summary += (" (no precise matches — showing top picks for your "
                        "group instead)")
        elif dropped:
            st.session_state.browse_filters = kept
            dropped_names = ", ".join(_filter_pretty(f) for f in dropped)
            summary += f" (relaxed: {dropped_names})"
        else:
            st.session_state.browse_filters = kept
    elif parsed["anchors"]:
        st.session_state.browse_anchor_name = parsed["anchors"][0]

    if summary:
        st.session_state["browse_summary"] = summary
    st.rerun()


def _render_like_path(all_games, party, family):
    """
    Unified "tell me what you want" input. Smart routing:
      - If the input fuzzy-matches a game name strongly, treat as anchor
        (use BGG fans-also-like + content similarity).
      - Else, send to Claude for natural-language parsing into structured
        filters + optional anchor games.
    """
    if (st.session_state.browse_anchor_name is None
            and not st.session_state.browse_filters):
        st.markdown("<div class='browse-title'>Tell me what you want</div>",
                    unsafe_allow_html=True)
        st.markdown(
            "<div class='browse-help'>Name a game you love, or describe "
            "what you're in the mood for — e.g. <em>'a deck builder with a "
            "western theme'</em>.</div>",
            unsafe_allow_html=True,
        )

        with st.form(key="freeform_form", clear_on_submit=False):
            query = st.text_input(
                "Game name or vibe", key="like_query",
                placeholder="Catan… or: cooperative & not too long",
                label_visibility="collapsed",
            )
            submitted = st.form_submit_button("Search", use_container_width=True)

        if submitted and query:
            _handle_freeform_query(query, all_games)
            return

        # Light-weight live preview: if user typed a clear anchor, show it
        if query and not submitted:
            hits = fuzzy_find_anchor(query, all_games=all_games)
            strong = [g for g in hits if g["name"].lower() == query.lower()]
            if strong:
                st.markdown(
                    "<div class='browse-help'>Looks like a game name — "
                    "tap to confirm:</div>",
                    unsafe_allow_html=True,
                )
                for g in strong[:3]:
                    if st.button(g["name"], key=f"quick_anchor_{g['game_id']}",
                                 use_container_width=True):
                        st.session_state.browse_anchor_name = g["name"]
                        _save_pref("like_anchor", g["name"])
                        st.rerun()
        return

    anchor_name = st.session_state.browse_anchor_name
    user_id = st.session_state.get("rec_user_id")
    user_themes = popular_themes_for_user(user_id) if user_id else None
    anchor, similar = find_similar(anchor_name, party_size=party,
                                    family_filter=family,
                                    user_themes=user_themes,
                                    limit=6, all_games=all_games)
    if anchor is None:
        st.error(f"Couldn't find '{anchor_name}' in our library.")
        if st.button("← Try a different game", key="back_like",
                     use_container_width=True):
            st.session_state.browse_anchor_name = None
            st.rerun()
        return

    st.markdown(f"<div class='browse-title'>Games like {anchor['name']}</div>",
                unsafe_allow_html=True)
    if st.button("← Try a different game", key="back_like_top",
                 use_container_width=True):
        st.session_state.browse_anchor_name = None
        st.rerun()
    if not similar:
        st.markdown(
            "<div class='browse-help'>No close matches available right now "
            "for this group.</div>",
            unsafe_allow_html=True,
        )
        return
    _render_game_list(similar)


def _render_game_list(games):
    """Compact list of game cards. One full-width tap to open detail."""
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

        # The card is a single full-width button labeled with title + meta.
        label = f"**{g['name']}**\n\n{meta}"
        if st.button(label, key=f"game_{g['game_id']}",
                     use_container_width=True):
            st.session_state.browse_detail_game_id = g["game_id"]
            st.session_state.browse_show_full_desc = False
            go_to_step("detail")


# ---------------------------------------------------------------------------
# Step 5: Game detail
# ---------------------------------------------------------------------------

def _render_detail():
    game_id = st.session_state.browse_detail_game_id
    g = get_game_by_id(game_id)
    if not g:
        st.error("Game not found.")
        if st.button("← Back", key="detail_back_err"):
            back_step()
        return

    short = g.get("short_description") or _strip_html(g.get("description") or "", 220)

    st.markdown(f"<div class='browse-title'>{g['name']}</div>", unsafe_allow_html=True)
    if g.get("image_url"):
        st.image(g["image_url"], use_container_width=True)

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

    # Primary actions — stacked, full width
    if st.button(f"🎯 Pick {g['name']}", key="pick_game",
                 use_container_width=True, type="primary"):
        _pick_game(g)
        st.success(f"Staff notified — bringing {g['name']} to your table.")

    if g.get("bgg_id"):
        if st.button("✨ More like this", key="more_like",
                     use_container_width=True):
            st.session_state.browse_hub_path = "like"
            st.session_state.browse_anchor_name = g["name"]
            st.session_state.browse_filters = []  # like-X is its own mode
            st.session_state.browse_detail_game_id = None
            go_to_step("list")

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
    """Log a game pick: writes to recommendation_log + triggers staff ping."""
    user_id = _ensure_rec_user_id()
    visit_id = st.session_state.get("visit_id")
    if user_id and visit_id:
        log_recommendation(
            session_id=visit_id, user_id=user_id, game_id=game["game_id"],
            score=1.0, breakdown={"reason": "browse_pick"},
            version="browse-v1", position=1,
        )
        mark_recommendation_selected(visit_id, game["game_id"])
    # Defer staff ping to existing app.py mechanism
    st.session_state._pending_ping = {
        "idx": None,
        "reason": "new_game",
        "extra": {"game_name": game["name"]},
    }
    st.session_state._ping_dialog_opened = False


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_browse_ui():
    """Render the full browse experience. Called from app.py main()."""
    _ensure_state()
    st.markdown(MOBILE_CSS, unsafe_allow_html=True)

    # Sticky-ish back / exit row at the top
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
    elif step == "family":
        _render_family()
    elif step == "hub":
        _render_hub()
    elif step == "list":
        _render_list()
    elif step == "detail":
        _render_detail()
    else:
        st.warning(f"Unknown step: {step}")
        if st.button("Reset"):
            reset_browse_state()
            st.rerun()
