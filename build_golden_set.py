"""
Build a golden Q&A set from public rules-Q&A sources.

Per game, tries sources in order until it hits QUESTIONS_PER_GAME:
  1. StackExchange "boardgames" site — moderated Q&A with accepted answers,
     mapped to a game via tag lookup. Highest signal-to-noise.
  2. Reddit search — pulls candidate posts across all subreddits (per-game
     subs + r/boardgames), grabs the top-scored non-OP reply as the answer.

Every kept question is filtered through Claude with the same rule as before:
"anything a rulebook could answer" (rules, edge cases, setup, components).

Output: `golden_set/<slug>.json` per game with metadata + questions.
Each question record has `source` = "stackexchange" | "reddit" so you can
weight them differently downstream.

Usage:
  python build_golden_set.py --games "Wingspan" "Catan"
  python build_golden_set.py --sample 100 --seed 42
"""
import argparse
import json
import os
import random
import re
import sys
import time
from html import unescape
from pathlib import Path

import requests
from anthropic import Anthropic
from dotenv import load_dotenv

from config import CLAUDE_MODEL
from database import get_all_games

load_dotenv(override=True)


OUTPUT_DIR = Path("golden_set")
QUESTIONS_PER_GAME = 10
MIN_ANSWER_CHARS = 60
MAX_ANSWER_CHARS = 3000
MAX_QUESTION_CHARS = 1200

USER_AGENT = "MerryMeepleRulesAssistant/1.0 (cpdelehanty@gmail.com)"

SE_API = "https://api.stackexchange.com/2.3"
SE_SITE = "boardgames"

REDDIT_BASE = "https://www.reddit.com"


# --------------------------------------------------------------------------
# Text cleaning
# --------------------------------------------------------------------------

def strip_html(text):
    """Strip HTML entities, tags, collapse whitespace."""
    if not text:
        return ""
    text = unescape(text)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"</p>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\r?\n[ \t]*\r?\n+", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def truncate(text, n):
    if not text:
        return ""
    text = text.strip()
    return text if len(text) <= n else text[:n].rstrip() + "..."


def normalize_for_dedupe(text):
    """Lowercase, collapse whitespace, strip punctuation — for fuzzy dedupe."""
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).split()


def already_seen(question_text, kept):
    """Cheap dedupe: >= 60% word overlap with any kept question."""
    words = set(normalize_for_dedupe(question_text))
    if len(words) < 4:
        return False
    for prev in kept:
        prev_words = set(normalize_for_dedupe(prev["question"]))
        if not prev_words:
            continue
        overlap = len(words & prev_words) / max(len(words | prev_words), 1)
        if overlap >= 0.6:
            return True
    return False


# --------------------------------------------------------------------------
# StackExchange
# --------------------------------------------------------------------------

def se_get(path, params):
    p = {"site": SE_SITE, **params}
    r = requests.get(
        f"{SE_API}/{path}", params=p,
        headers={"User-Agent": USER_AGENT}, timeout=30,
    )
    r.raise_for_status()
    j = r.json()
    if j.get("backoff"):
        time.sleep(j["backoff"] + 0.5)
    return j


def _slug_variants(game_title):
    """Progressively broader slug variants to try as SE tag names.

    Only variants that clearly refer to the same game — never a first-word
    fallback that would drag in unrelated games (Ark Nova → Arkham Horror).
    """
    base = re.sub(r"[^a-z0-9\s-]+", "", game_title.lower())
    base_words = base.split()
    variants = []
    # Full title with hyphens
    variants.append("-".join(base_words))
    # Drop trailing edition/expansion tag: 'wingspan asia' → 'wingspan'
    if len(base_words) > 1 and ":" in game_title:
        head = game_title.split(":")[0].lower().strip()
        variants.append("-".join(re.sub(r"[^a-z0-9\s]+", "", head).split()))
    # De-dupe while preserving order
    seen = set()
    out = []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def se_find_tag(game_title):
    """Find the boardgames.SE tag for a title. Requires an EXACT tag match on
    a slug variant of the game name — no first-word fallback."""
    for candidate in _slug_variants(game_title):
        try:
            j = se_get("tags", {
                "inname": candidate,
                "order": "desc", "sort": "popular", "pagesize": 20,
            })
        except Exception:
            continue
        for it in j.get("items", []):
            if it["name"] == candidate:
                return it["name"]
    return None


def se_get_questions_for_tag(tag, limit=30):
    """Fetch answered questions for a tag, sorted by votes."""
    j = se_get("questions", {
        "tagged": tag,
        "order": "desc", "sort": "votes",
        "pagesize": limit,
        "filter": "withbody",  # include body of each question
    })
    return j.get("items", [])


def se_get_answers_bulk(answer_ids):
    """Batch-fetch answer bodies. SE accepts up to 100 IDs joined by `;`."""
    if not answer_ids:
        return {}
    id_str = ";".join(str(x) for x in answer_ids[:100])
    j = se_get(f"answers/{id_str}", {"filter": "withbody"})
    return {it["answer_id"]: it.get("body", "") for it in j.get("items", [])}


def fetch_stackexchange(game_title, limit=15):
    """Return list of question records from boardgames.SE for this game."""
    tag = se_find_tag(game_title)
    if not tag:
        return []

    try:
        questions = se_get_questions_for_tag(tag, limit=limit)
    except Exception as e:
        print(f"    SE fetch failed: {e}")
        return []

    # Collect accepted answer IDs, batch-fetch bodies in one call
    answered = [q for q in questions
                if q.get("is_answered") and q.get("accepted_answer_id")]
    accepted_ids = [q["accepted_answer_id"] for q in answered]
    try:
        answer_bodies = se_get_answers_bulk(accepted_ids)
    except Exception as e:
        print(f"    SE bulk answers fetch failed: {e}")
        return []

    out = []
    for q in answered:
        answer_html = answer_bodies.get(q["accepted_answer_id"], "")
        answer = strip_html(answer_html)
        if len(answer) < MIN_ANSWER_CHARS:
            continue
        title = q.get("title", "").strip()
        body = strip_html(q.get("body", ""))
        question_text = truncate(f"{title}\n\n{body}".strip(), MAX_QUESTION_CHARS)
        out.append({
            "question": question_text,
            "top_answer": truncate(answer, MAX_ANSWER_CHARS),
            "thread_subject": title,
            "source_url": q.get("link", ""),
            "source": "stackexchange",
            "score": q.get("score", 0),
        })
    return out


# --------------------------------------------------------------------------
# Reddit
# --------------------------------------------------------------------------

def reddit_get(path, params=None):
    r = requests.get(
        f"{REDDIT_BASE}{path}",
        params=params,
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    r.raise_for_status()
    time.sleep(1.2)  # be polite; Reddit throttles anon
    return r.json()


def reddit_search(game_title, limit=25):
    """Search Reddit for rules-question posts about the game."""
    query = f'"{game_title}" rules'
    try:
        j = reddit_get("/search.json", {
            "q": query, "sort": "relevance", "limit": limit,
            "type": "link", "restrict_sr": 0,
        })
    except Exception as e:
        print(f"    Reddit search failed: {e}")
        return []
    return [child.get("data", {}) for child in j.get("data", {}).get("children", [])]


def reddit_get_top_comment(post_id):
    """Fetch a Reddit post's top-level comment (highest score, not by OP)."""
    try:
        payload = reddit_get(f"/comments/{post_id}.json")
    except Exception as e:
        print(f"    Reddit comments {post_id} failed: {e}")
        return None, None
    if not isinstance(payload, list) or len(payload) < 2:
        return None, None
    post_listing = payload[0].get("data", {}).get("children", [])
    op = post_listing[0]["data"]["author"] if post_listing else None
    comments = payload[1].get("data", {}).get("children", [])
    best = None
    for c in comments:
        d = c.get("data", {})
        if d.get("stickied") or d.get("author") == "AutoModerator":
            continue
        if d.get("author") == op:
            continue
        body = d.get("body", "").strip()
        if len(body) < MIN_ANSWER_CHARS:
            continue
        score = d.get("score", 0)
        if best is None or score > best["score"]:
            best = {"body": body, "score": score, "author": d.get("author", "")}
    if not best:
        return None, None
    return best["body"], best["author"]


def fetch_reddit(game_title, limit=25):
    """Return list of question records from Reddit for this game."""
    posts = reddit_search(game_title, limit=limit)
    out = []
    title_lower = game_title.lower()
    for p in posts:
        subreddit = p.get("subreddit", "")
        if subreddit.lower() in {"boardgamegeek"}:  # BGG mirror, may be duplicative
            continue
        title = p.get("title", "")
        selftext = p.get("selftext", "")
        # Require the post to genuinely be about the game (title mentions it)
        blob = f"{title} {selftext}".lower()
        if title_lower not in blob:
            continue
        # Prefer posts with the game's name in the title so we don't grab
        # random "boardgames general" posts.
        if p.get("num_comments", 0) < 1:
            continue

        post_id = p.get("id", "")
        top_body, top_author = reddit_get_top_comment(post_id)
        if not top_body:
            continue

        question_text = truncate(f"{title}\n\n{selftext}".strip(), MAX_QUESTION_CHARS)
        permalink = p.get("permalink", "")
        out.append({
            "question": question_text,
            "top_answer": truncate(top_body, MAX_ANSWER_CHARS),
            "thread_subject": title,
            "source_url": f"{REDDIT_BASE}{permalink}",
            "source": f"reddit /r/{subreddit}",
            "score": p.get("score", 0),
            "reply_by": top_author,
        })
    return out


# --------------------------------------------------------------------------
# Claude filter
# --------------------------------------------------------------------------

CLASSIFY_PROMPT = """You are filtering forum questions for a golden Q&A set.

Reply "yes" if the question is a RULES question — anything a rulebook could
answer: rules, edge cases, setup, component counts, clarifications, timing
questions.

Reply "no" if it's strategy advice, opinion, review, session report,
house-rule brainstorm, expansion review, buying advice, or any non-rules-
question meta/community topic.

Thread subject and body:
---
{question}
---

Reply with just one word: yes or no."""


def is_rules_question(question_text, anthropic_client):
    if not question_text:
        return False
    truncated = truncate(question_text, 1500)
    try:
        resp = anthropic_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=8,
            messages=[{"role": "user",
                       "content": CLASSIFY_PROMPT.format(question=truncated)}],
        )
    except Exception as e:
        print(f"    classify error: {e}")
        return False
    return resp.content[0].text.strip().lower().startswith("y")


# --------------------------------------------------------------------------
# Per-game pipeline
# --------------------------------------------------------------------------

def slugify(title):
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def build_for_game(game_title, anthropic_client):
    kept = []
    sources_tried = []

    # Reddit is currently dead for anonymous access (403 across all endpoints).
    # If Casey registers a Reddit OAuth app later, add ("reddit", fetch_reddit) back.
    for fetcher_name, fetcher in (
        ("stackexchange", fetch_stackexchange),
    ):
        if len(kept) >= QUESTIONS_PER_GAME:
            break
        print(f"    trying {fetcher_name}...")
        try:
            candidates = fetcher(game_title, limit=25)
        except Exception as e:
            print(f"      {fetcher_name} error: {e}")
            candidates = []
        sources_tried.append({"name": fetcher_name, "candidates": len(candidates)})

        for cand in candidates:
            if len(kept) >= QUESTIONS_PER_GAME:
                break
            if already_seen(cand["question"], kept):
                continue
            if not is_rules_question(cand["question"], anthropic_client):
                continue
            kept.append(cand)
            print(f"      [{len(kept)}/{QUESTIONS_PER_GAME}] "
                  f"{cand['thread_subject'][:70]}")

    result = {
        "game": game_title,
        "questions": kept,
        "sources_tried": sources_tried,
    }
    if not kept:
        result["error"] = "no rules questions found in any source"
    return result


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--games", nargs="*",
                        help="Explicit game titles (overrides --sample)")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(exist_ok=True)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)
    anthropic_client = Anthropic(api_key=api_key)

    if args.games:
        games = args.games
    else:
        all_games = [g["title"] for g in get_all_games()]
        rng = random.Random(args.seed)
        games = rng.sample(all_games, min(args.sample, len(all_games)))

    print(f"Building golden set for {len(games)} game(s)")
    print(f"Output: {OUTPUT_DIR}/\n")

    for i, title in enumerate(games, 1):
        slug = slugify(title)
        outpath = OUTPUT_DIR / f"{slug}.json"
        if outpath.exists() and not args.overwrite:
            print(f"[{i}/{len(games)}] {title}: SKIP (exists)")
            continue

        print(f"[{i}/{len(games)}] {title}")
        try:
            data = build_for_game(title, anthropic_client)
        except Exception as e:
            data = {"game": title, "error": f"exception: {e}"}

        with open(outpath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        n = len(data.get("questions", []))
        err = data.get("error", "")
        summary = f"  → {n} question(s)"
        if err:
            summary += f" ({err})"
        print(summary + "\n")


if __name__ == "__main__":
    main()
