"""
Rules-assistant Q&A pipeline — shared between production app and the
groundedness test harness so both exercise identical retrieval + answer
+ critique logic.

Pipeline:
    question
      -> embed_query (Voyage)
      -> top_k_cosine (in-memory)        :: top 15 by similarity
      -> rerank_with_claude               :: top 8 by relevance (optional)
      -> answer_with_chunks (Claude)
      -> self_critique (Claude, optional) :: corrects confident-wrong answers

Flags (defaults match production):
    use_rerank      = True   (catches Mode A: chunks have answer, retrieval ranks them low)
    use_critique    = True   (catches Mode C: hallucinated facts not in chunks)
    cosine_top_k    = 15
    rerank_top_k    = 8
"""
import json
import re
import time

import numpy as np


VOYAGE_MODEL = "voyage-3"
CLAUDE_MODEL = "claude-sonnet-4-20250514"

COSINE_TOP_K = 15
RERANK_TOP_K = 8


# ---------------------------------------------------------------------------
# Retrieval primitives
# ---------------------------------------------------------------------------

def cosine(a, b):
    a, b = np.array(a), np.array(b)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def embed_query(voyage_client, text):
    """One query embed with retries."""
    last = None
    for attempt in range(4):
        try:
            r = voyage_client.embed(texts=[text], model=VOYAGE_MODEL,
                                     input_type="query")
            return r.embeddings[0]
        except Exception as e:
            last = e
            wait = 30 * (attempt + 1)
            time.sleep(wait)
    raise RuntimeError(f"voyage failed after retries: {last}")


def top_k_cosine(question_emb, chunks, k=COSINE_TOP_K):
    """Return top-k chunks by cosine similarity to the question."""
    scored = [(cosine(question_emb, c["embedding"]), c) for c in chunks]
    scored.sort(reverse=True, key=lambda x: x[0])
    return [c for _, c in scored[:k]]


# ---------------------------------------------------------------------------
# Claude calls
# ---------------------------------------------------------------------------

def claude_call(client, prompt, max_tokens=1500):
    """Single Claude call with retry on rate-limit / overload."""
    from anthropic import APIStatusError
    last = None
    for attempt in range(4):
        try:
            r = client.messages.create(
                model=CLAUDE_MODEL, max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return r.content[0].text
        except APIStatusError as e:
            last = e
            if e.status_code in (429, 529):
                time.sleep(2 ** (attempt + 2))
                continue
            raise
        except Exception as e:
            last = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"claude failed after retries: {last}")


def _parse_json_obj(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip("json\n").strip()
    s, e = text.find("{"), text.rfind("}")
    if s < 0:
        return None
    try:
        return json.loads(text[s:e + 1])
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Re-ranking — turns top-15 cosine into top-K by Claude-judged relevance
# ---------------------------------------------------------------------------

def rerank_with_claude(claude_client, question, chunks, k=RERANK_TOP_K):
    """
    Given top-N candidates by cosine, ask Claude to pick the K most
    directly relevant to the question. Returns the K selected chunks
    (preserves a fallback if the reranker output is malformed).
    """
    if len(chunks) <= k:
        return list(chunks)

    numbered = []
    for i, c in enumerate(chunks):
        snippet = (c.get("text") or "").strip().replace("\n", " ")[:400]
        numbered.append(f"[{i}] (page {c.get('page')}) {snippet}")
    candidate_block = "\n\n".join(numbered)

    prompt = f"""You are picking the most relevant rulebook excerpts to answer a player's question.

QUESTION: {question}

CANDIDATE EXCERPTS:
{candidate_block}

Pick the {k} excerpts that most directly help answer the question. Prefer
excerpts that describe the SPECIFIC rule, mechanic, phase, or component
the question is about — even if the exact words don't match.

Respond with JSON ONLY (no prose, no code fences):
{{"picks": [<list of {k} integers, in order of relevance>]}}"""
    try:
        raw = claude_call(claude_client, prompt, max_tokens=200)
        data = _parse_json_obj(raw)
        if data and isinstance(data.get("picks"), list):
            ids = [int(i) for i in data["picks"] if isinstance(i, int)
                    or (isinstance(i, str) and i.isdigit())]
            picked = [chunks[i] for i in ids if 0 <= i < len(chunks)]
            if len(picked) >= max(k - 2, 1):  # tolerant — at least k-2 valid picks
                return picked[:k]
    except Exception:
        pass
    # Fallback: cosine top-K
    return chunks[:k]


# ---------------------------------------------------------------------------
# Answer generation (matches production prompt in app.py)
# ---------------------------------------------------------------------------

def build_context(top_chunks):
    parts = []
    for c in top_chunks:
        label = {
            "rulebook": "Rulebook", "faq": "FAQ",
            "errata": "Errata", "supplement": "Supplement",
        }.get(c.get("source_type", "rulebook"), "Rulebook")
        parts.append(f"[{label} - Page {c['page']}]\n{c['text']}")
    return "\n\n---\n\n".join(parts)


def answer_with_chunks(claude_client, question, game_title, top_chunks):
    """
    Generate an answer using ONLY the provided chunks. Mirrors the prompt
    at app.py's answer_question for the rules-only path.
    """
    context = build_context(top_chunks)
    prompt = f"""You are a helpful board game rules assistant at The Merry Meeple cafe.
Answer the customer's question based ONLY on the source documents provided below.

Provide a clear, direct answer to the specific question asked.

Rules for answering:
- Be friendly and conversational
- When citing information, include BOTH the source type AND page number
  Example: "The rulebook states each player draws 5 cards (Rulebook p. 3)"
- If information comes from multiple sources, cite all
- If the answer isn't in any of the provided sources, say "I don't see that
  information in the materials I have access to. Would you like me to
  request staff assistance?"
- If the question is unclear, ask ONE clarifying question
- Never make up rules that aren't in the source documents

SOURCE DOCUMENTS FOR {game_title.upper()}:
{context}

CUSTOMER QUESTION: {question}

YOUR ANSWER:"""
    return claude_call(claude_client, prompt, max_tokens=1500)


# ---------------------------------------------------------------------------
# Self-critique — second pass that checks fidelity to the chunks
# ---------------------------------------------------------------------------

def self_critique(claude_client, question, top_chunks, answer):
    """
    Ask Claude to verify the answer against the chunks. If a fact is
    invented or misread, return a corrected answer. If the answer is
    faithful, return the original unchanged.
    """
    chunks_text = "\n\n---\n\n".join(
        f"[Page {c['page']}] {c['text']}" for c in top_chunks
    )
    prompt = f"""You are checking a board-game rules-assistant's answer for FIDELITY to the rulebook chunks. Your job is conservative: only flag claims that are clearly unsupported.

QUESTION: {question}

RULEBOOK CHUNKS (the only valid source):
{chunks_text}

PROPOSED ANSWER:
{answer}

Default verdict is "ok". Only flag a claim as unsupported if BOTH:
  (a) the chunks do not contain it, AND
  (b) the chunks do not clearly imply it via a stated rule

Specific things that ARE unsupported:
  - Quantities that don't match the chunk exactly (e.g. "14 cubes" when chunk says "4 + 10").
  - Confusing "once per round" with "once per turn", "optional" with "mandatory".
  - Inventing component names, mechanics, or rules not in the chunks.

Specific things that are NOT unsupported (do not flag these):
  - Paraphrasing the chunk in different words.
  - Reasonable summary of multiple chunk sections.
  - Acknowledging "the chunks don't say" when answering edge-case questions.
  - Tone or structure differences.

If the answer is faithful, return it UNCHANGED — do NOT add details, expand explanations, or rephrase. Even if you could improve it, don't.

If a claim IS unsupported, remove ONLY that specific claim. Keep everything else (citations, tone, structure) exactly as written.

Respond with JSON ONLY (no prose, no code fences):
{{"verdict": "ok"|"corrected", "answer": "<final answer text>"}}"""
    try:
        raw = claude_call(claude_client, prompt, max_tokens=2000)
        data = _parse_json_obj(raw)
        if data and isinstance(data.get("answer"), str):
            return data["answer"]
    except Exception:
        pass
    return answer  # safe fallback: keep original


# ---------------------------------------------------------------------------
# End-to-end driver
# ---------------------------------------------------------------------------

def run_pipeline(voyage_client, claude_client, question, game_title, chunks,
                 use_rerank=True, use_critique=True,
                 cosine_top_k=COSINE_TOP_K, rerank_top_k=RERANK_TOP_K):
    """
    Full Q&A pipeline. Returns dict:
        {
          "answer": str,
          "top_chunks": [...],   # chunks fed to the answer step
          "rerank_used": bool,
          "critique_changed": bool,
        }
    """
    emb = embed_query(voyage_client, question)
    candidates = top_k_cosine(emb, chunks, k=cosine_top_k)

    if use_rerank and len(candidates) > rerank_top_k:
        top = rerank_with_claude(claude_client, question, candidates, k=rerank_top_k)
        rerank_used = True
    else:
        top = candidates[:rerank_top_k]
        rerank_used = False

    answer = answer_with_chunks(claude_client, question, game_title, top)

    critique_changed = False
    if use_critique:
        new_answer = self_critique(claude_client, question, top, answer)
        if new_answer != answer:
            critique_changed = True
            answer = new_answer

    return {
        "answer": answer,
        "top_chunks": top,
        "rerank_used": rerank_used,
        "critique_changed": critique_changed,
    }
