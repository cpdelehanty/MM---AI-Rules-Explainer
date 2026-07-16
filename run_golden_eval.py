"""
Run every golden-set question through the rules-assistant RAG pipeline and
record the bot's answer alongside the community-accepted answer for review.

Uses the SAME prompt + retrieval as app.py — this is a straight reproduction
of what a customer would get today, not an offline judge.

Voyage query embeddings are batched (up to 128 per call) so the whole run
takes ~1 min of embeds + ~15 min of Claude answers.

Output:
  golden_results.json   — full structured records
  golden_results.md     — side-by-side markdown for human review
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import voyageai
from anthropic import Anthropic, APIStatusError
from dotenv import load_dotenv

from config import CLAUDE_MODEL, VOYAGE_MODEL
from database import get_chunks_including_parent
from retrieval import hybrid_search, hybrid_with_rerank

load_dotenv(override=True)


TOP_K = 8                   # bumped from 5 — wider context, marginal cost
VOYAGE_BATCH = 100          # batch size for embed calls (Voyage supports up to 128)
VOYAGE_BATCH_DELAY = 25     # seconds between embed batches (3 RPM free tier)
CLAUDE_MAX_TOKENS = 1500


# --------------------------------------------------------------------------
# RAG helpers (mirror app.py — kept independent so we don't import Streamlit)
# --------------------------------------------------------------------------

def cosine(v1, v2):
    v1, v2 = np.array(v1), np.array(v2)
    return float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))


def search_baseline(query, query_emb, chunks, claude_client=None, top_k=TOP_K):
    """Semantic-only cosine top-k (the original production behavior)."""
    scored = [(cosine(query_emb, c["embedding"]), c) for c in chunks]
    scored.sort(reverse=True, key=lambda x: x[0])
    return [c for _, c in scored[:top_k]]


def search_hybrid(query, query_emb, chunks, claude_client=None, top_k=TOP_K):
    """BM25 + semantic blended via RRF."""
    return hybrid_search(query, query_emb, chunks, top_k=top_k)


def search_hybrid_rerank(query, query_emb, chunks, claude_client=None, top_k=TOP_K):
    """Hybrid top-15 → Claude rerank → top-8."""
    return hybrid_with_rerank(query, query_emb, chunks, claude_client, top_k=top_k)


SEARCH_MODES = {
    "baseline": search_baseline,
    "hybrid": search_hybrid,
    "hybrid+rerank": search_hybrid_rerank,
}


def build_rules_prompt(question, game_title, top_chunks):
    context_parts = []
    for c in top_chunks:
        label = {
            "rulebook": "Rulebook",
            "faq": "FAQ",
            "errata": "Errata",
            "supplement": "Supplement",
        }.get(c.get("source_type", "rulebook"), "Rulebook")
        origin = c.get("game_source")
        origin_tag = f" [{origin}]" if origin and origin != game_title else ""
        context_parts.append(
            f"[{label}{origin_tag} - Page {c['page']}]\n{c['text']}"
        )
    context = "\n\n---\n\n".join(context_parts)

    setup_kw = ["setup", "set up", "start", "beginning", "prepare",
                "how to play", "getting started"]
    is_setup = any(kw in question.lower() for kw in setup_kw)

    if is_setup:
        instruction = (
            "This is a SETUP question. Provide a complete, step-by-step walkthrough "
            "of the setup process. Use numbered steps, cover all components that need "
            "to be placed, mention player-specific setup, and cover any special setup "
            "rules for different player counts if mentioned."
        )
    else:
        instruction = "Provide a clear, direct answer to the specific question asked."

    return f"""You are a helpful board game rules assistant at The Merry Meeple cafe. \
Answer the customer's question about **{game_title}** based ONLY on the source documents \
provided below.

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


def call_claude(anthropic_client, prompt, max_retries=3):
    for attempt in range(max_retries):
        try:
            resp = anthropic_client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=CLAUDE_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.content[0].text
        except APIStatusError as e:
            if e.status_code in (429, 529) and attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"    Claude {e.status_code}, waiting {wait}s...")
                time.sleep(wait)
            else:
                raise


# --------------------------------------------------------------------------
# Golden-set loading
# --------------------------------------------------------------------------

def load_rows():
    """Load every question record from golden_set/*.json."""
    rows = []
    for f in sorted(Path("golden_set").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        game = d["game"]
        for q in d.get("questions", []):
            rows.append({
                "game": game,
                "question": q["question"],
                "community_answer": q.get("top_answer", ""),
                "source_url": q.get("source_url", ""),
                "thread_subject": q.get("thread_subject", ""),
                "community_source": q.get("source", ""),
            })
    return rows


def embed_all(voyage_client, questions):
    """Batch-embed all query texts. Returns list[list[float]] matched to questions."""
    all_embs = []
    total = len(questions)
    for i in range(0, total, VOYAGE_BATCH):
        batch = questions[i:i + VOYAGE_BATCH]
        print(f"  embedding batch {i//VOYAGE_BATCH + 1} "
              f"({len(batch)} of {total} remaining)...")
        result = voyage_client.embed(
            texts=batch, model=VOYAGE_MODEL, input_type="query"
        )
        all_embs.extend(result.embeddings)
        if i + VOYAGE_BATCH < total:
            print(f"  waiting {VOYAGE_BATCH_DELAY}s (Voyage 3 RPM free tier)...")
            time.sleep(VOYAGE_BATCH_DELAY)
    return all_embs


# --------------------------------------------------------------------------
# Markdown output
# --------------------------------------------------------------------------

def _quoteify(text, max_chars=1500):
    """Truncate + turn newlines into markdown blockquote continuations."""
    text = (text or "").strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."
    return text.replace("\n", "\n> ")


def write_markdown(rows, path):
    by_game = defaultdict(list)
    for r in rows:
        by_game[r["game"]].append(r)

    with open(path, "w", encoding="utf-8") as f:
        f.write("# Golden Set — Bot vs. Community Answers\n\n")
        f.write(f"{len(rows)} question(s) across {len(by_game)} game(s). "
                f"Questions sourced from boardgames.stackexchange.com; "
                f"answers below are (a) the accepted-answer from SE, and "
                f"(b) our rules assistant's answer using the same RAG pipeline "
                f"as production.\n\n")
        for game in sorted(by_game):
            f.write(f"---\n\n## {game}\n\n")
            for i, r in enumerate(by_game[game], 1):
                subject = r.get("thread_subject") or r["question"].split("\n")[0]
                f.write(f"### Q{i}. {subject[:120]}\n\n")
                f.write(f"**Question:**\n\n> {_quoteify(r['question'], 800)}\n\n")
                f.write(f"**Community answer** "
                        f"([source]({r['source_url']})):\n\n")
                f.write(f"> {_quoteify(r['community_answer'], 1500)}\n\n")
                pages = r.get("source_pages") or []
                page_hint = (f" (sources: pages {', '.join(str(p) for p in pages)})"
                             if pages else "")
                f.write(f"**Bot answer**{page_hint}:\n\n")
                f.write(f"> {_quoteify(r.get('bot_answer', ''), 1800)}\n\n")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=list(SEARCH_MODES),
                        default="hybrid",
                        help="Retrieval mode to evaluate.")
    parser.add_argument("--output", default="golden_results.json",
                        help="Output JSON file (Markdown derived automatically).")
    args = parser.parse_args()

    if not os.environ.get("VOYAGE_API_KEY") or not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: VOYAGE_API_KEY and ANTHROPIC_API_KEY required in .env")
        sys.exit(1)

    voyage_client = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])
    anthropic_client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    search_fn = SEARCH_MODES[args.mode]
    print(f"Retrieval mode: {args.mode} (top_k={TOP_K})\n")

    rows = load_rows()
    if not rows:
        print("No rows in golden_set/*.json — nothing to do.")
        return

    print(f"Loaded {len(rows)} question(s) across "
          f"{len({r['game'] for r in rows})} game(s).\n")

    print("Embedding queries with Voyage AI...")
    embs = embed_all(voyage_client, [r["question"] for r in rows])
    print(f"Got {len(embs)} embeddings.\n")

    chunk_cache = {}
    def get_chunks(game):
        if game not in chunk_cache:
            chunk_cache[game] = get_chunks_including_parent(game) or []
        return chunk_cache[game]

    print("Generating bot answers...")
    for i, (row, emb) in enumerate(zip(rows, embs), 1):
        chunks = get_chunks(row["game"])
        if not chunks:
            row["bot_answer"] = "(no chunks indexed for this game)"
            row["source_pages"] = []
            continue
        top = search_fn(row["question"], emb, chunks,
                        claude_client=anthropic_client, top_k=TOP_K)
        prompt = build_rules_prompt(row["question"], row["game"], top)
        try:
            row["bot_answer"] = call_claude(anthropic_client, prompt)
        except Exception as e:
            row["bot_answer"] = f"(error: {e})"
        row["source_pages"] = sorted({c["page"] for c in top})
        if i % 10 == 0 or i == len(rows):
            print(f"  {i}/{len(rows)}")

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {args.output} ({len(rows)} rows)")

    md_path = args.output.replace(".json", ".md")
    write_markdown(rows, md_path)
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
