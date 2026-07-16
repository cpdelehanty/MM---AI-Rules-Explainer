"""
Hybrid retrieval for rules Q&A.

Combines Voyage semantic embeddings (cosine similarity) with BM25 lexical
matching via Reciprocal Rank Fusion (RRF). RRF is the standard hybrid-search
technique: no score normalization needed, and it naturally handles cases
where either method dominates for a given query.

  - Semantic wins on paraphrase and conceptual questions
  - BM25 wins when the question uses specific game terminology (proper
    nouns, mechanic names) that a general embedding under-weights

Optional second stage: pass the wider candidate set to a Claude reranker
(from rules_pipeline.rerank_with_claude) for finer ordering.
"""
import re

import numpy as np
from rank_bm25 import BM25Okapi


TOP_K = 8            # final chunks fed to the answer prompt
CANDIDATE_K = 15     # wider net when reranking is enabled
RRF_K = 60           # RRF smoothing constant (60 is the paper default)


def _tokenize(text):
    """Basic word tokenizer — lowercase, alphanumeric-only."""
    return re.findall(r"\w+", (text or "").lower())


def cosine(v1, v2):
    v1 = np.asarray(v1)
    v2 = np.asarray(v2)
    return float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))


def semantic_ranks(query_embedding, chunks):
    """Return {chunk_index: rank (0=best)} by cosine similarity."""
    scores = [cosine(query_embedding, c["embedding"]) for c in chunks]
    order = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)
    return {i: r for r, i in enumerate(order)}, scores


def bm25_ranks(query, chunks):
    """Return {chunk_index: rank (0=best)} by BM25 score."""
    tokenized = [_tokenize(c["text"]) for c in chunks]
    if not any(tokenized):
        # All-empty texts — nothing to rank
        return {i: len(chunks) for i in range(len(chunks))}, [0.0] * len(chunks)
    bm25 = BM25Okapi(tokenized)
    scores = bm25.get_scores(_tokenize(query))
    order = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)
    return {i: r for r, i in enumerate(order)}, list(scores)


def hybrid_search(query, query_embedding, chunks, top_k=TOP_K, rrf_k=RRF_K):
    """
    Return the top_k chunks by Reciprocal Rank Fusion of semantic + BM25.

    RRF score for chunk i = 1/(rrf_k + sem_rank[i]) + 1/(rrf_k + bm_rank[i])

    A chunk that ranks 1st in either method gets a big boost even if it's
    ranked low in the other. This lets each retrieval mode contribute its
    strengths without either being able to veto the other.
    """
    if not chunks:
        return []

    sem_rank, _ = semantic_ranks(query_embedding, chunks)
    bm_rank, _ = bm25_ranks(query, chunks)

    rrf = {
        i: 1.0 / (rrf_k + sem_rank[i]) + 1.0 / (rrf_k + bm_rank[i])
        for i in range(len(chunks))
    }
    order = sorted(range(len(chunks)), key=lambda i: rrf[i], reverse=True)
    return [chunks[i] for i in order[:top_k]]


def hybrid_with_rerank(query, query_embedding, chunks, claude_client,
                       candidate_k=CANDIDATE_K, top_k=TOP_K):
    """
    Two-stage retrieval:
      1. RRF-blend semantic + BM25 → top candidate_k candidates
      2. Ask Claude to pick the top_k most directly relevant from those

    Adds one Claude call per question but often meaningfully improves top-K
    quality on ambiguous questions.
    """
    candidates = hybrid_search(query, query_embedding, chunks, top_k=candidate_k)
    if len(candidates) <= top_k:
        return candidates

    # Reuse the existing reranker
    from rules_pipeline import rerank_with_claude
    return rerank_with_claude(claude_client, query, candidates, k=top_k)
