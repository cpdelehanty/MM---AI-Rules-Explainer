"""
Groundedness Evaluation for Merry Meeple AI Responses

Tests whether AI-generated rules answers are grounded in the retrieved
context chunks. Uses Claude as a judge to score each factual claim.

Usage:
    python eval_groundedness.py              # Run all test cases
    python eval_groundedness.py --game Catan  # Run only Catan tests
    python eval_groundedness.py --verbose     # Show full claim details
"""

import json
import os
import sys
import time
import argparse
import numpy as np
import yaml
import voyageai
from anthropic import Anthropic
from dotenv import load_dotenv
from database import init_database, get_game_chunks

# Load .env - check current dir first, then walk up to find it
load_dotenv(override=True)
if not os.environ.get("ANTHROPIC_API_KEY"):
    search_dir = os.path.dirname(os.path.abspath(__file__))
    for _ in range(5):
        env_path = os.path.join(search_dir, ".env")
        if os.path.exists(env_path):
            load_dotenv(env_path, override=True)
            break
        search_dir = os.path.dirname(search_dir)

# --- Constants ---
TOP_K_RESULTS = 5
PASS_THRESHOLD = 0.95
WARN_THRESHOLD = 0.85
JUDGE_MODEL = "claude-sonnet-4-20250514"

# --- Chunk retrieval (mirrors app.py logic without importing Streamlit) ---

def cosine_similarity(vec1, vec2):
    vec1 = np.array(vec1)
    vec2 = np.array(vec2)
    return np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))

def search_chunks(query_embedding, chunks, top_k=TOP_K_RESULTS):
    similarities = []
    for chunk in chunks:
        sim = cosine_similarity(query_embedding, chunk["embedding"])
        similarities.append((sim, chunk))
    similarities.sort(reverse=True, key=lambda x: x[0])
    return [chunk for _, chunk in similarities[:top_k]]

def retrieve_context(question, game_title, voyage_client):
    """Retrieve top-K context chunks for a question (mirrors app.py pipeline)."""
    chunks = get_game_chunks(game_title)
    if not chunks:
        return [], ""

    query_embedding = voyage_client.embed(
        texts=[question],
        model="voyage-3",
        input_type="query"
    ).embeddings[0]

    top_chunks = search_chunks(query_embedding, chunks)

    context_parts = []
    for chunk in top_chunks:
        page = chunk["page"]
        source_type = chunk.get("source_type", "rulebook")
        source_label = {
            "rulebook": "Rulebook",
            "faq": "FAQ",
            "errata": "Errata",
            "supplement": "Supplement"
        }.get(source_type, "Rulebook")
        context_parts.append(f"[{source_label} - Page {page}]\n{chunk['text']}")

    context_str = "\n\n---\n\n".join(context_parts)
    return top_chunks, context_str

# --- Answer generation (mirrors app.py) ---

def generate_answer(question, game_title, context_str, anthropic_client):
    """Generate an answer using the same prompt template as app.py."""
    setup_keywords = ["setup", "set up", "start", "beginning", "prepare", "how to play", "getting started"]
    is_setup = any(kw in question.lower() for kw in setup_keywords)

    if is_setup:
        instruction = """This is a SETUP question. Provide a complete, step-by-step walkthrough of the setup process.
- Use numbered steps
- Be thorough and detailed
- Include all components that need to be placed
- Mention player-specific setup (what each player gets/does)
- Cover any special setup for different player counts if mentioned"""
    else:
        instruction = "Provide a clear, direct answer to the specific question asked."

    prompt = f"""You are a helpful board game rules assistant at The Merry Meeple cafe. Answer the customer's question based ONLY on the source documents provided below.

The sources may include:
- Rulebook (official game rules)
- FAQ (official frequently asked questions)
- Errata (official corrections/clarifications)
- Supplements (other official materials)

{instruction}

Rules for answering:
- Be friendly and conversational
- When citing information, include BOTH the source type AND page number
  Example: "According to the FAQ, nectar tokens can be spent as wild food (FAQ p. 2)"
- If information comes from multiple sources, cite all
- If the answer isn't in any of the provided sources, say "I don't see that information in the materials I have access to."
- Never make up rules that aren't in the source documents

SOURCE DOCUMENTS FOR {game_title.upper()}:
{context_str}

CUSTOMER QUESTION: {question}

YOUR ANSWER:"""

    response = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text

# --- Groundedness judge ---

JUDGE_PROMPT = """You are an expert evaluator assessing whether an AI-generated answer is grounded in the provided source context.

TASK: Break the answer into individual factual claims, then check each claim against the source context.

SOURCE CONTEXT (this is the ONLY valid evidence):
{context}

QUESTION: {question}

AI-GENERATED ANSWER:
{answer}

For each factual claim in the answer, determine:
- "supported": The claim is directly stated or clearly implied by the source context
- "partially_supported": The claim is related to something in the context but adds specifics not present
- "unsupported": The claim has no basis in the source context (this is a hallucination)

NOTE: Conversational filler ("Great question!", "Let me know if you need help") is NOT a factual claim — skip these.
NOTE: If the answer says "I don't see that information," that is correctly grounded — mark as supported.

Return ONLY valid JSON (no markdown fences):
{{
  "claims": [
    {{
      "claim": "brief description of the factual claim",
      "verdict": "supported" | "partially_supported" | "unsupported",
      "evidence": "relevant quote from context, or null if unsupported"
    }}
  ]
}}"""


def judge_groundedness(question, context_str, answer, anthropic_client):
    """Use Claude to evaluate whether the answer is grounded in context."""
    prompt = JUDGE_PROMPT.format(
        context=context_str,
        question=question,
        answer=answer
    )

    response = anthropic_client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        print(f"  [WARN] Judge returned invalid JSON, retrying...")
        # Retry once
        response2 = anthropic_client.messages.create(
            model=JUDGE_MODEL,
            max_tokens=4000,
            messages=[
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": text},
                {"role": "user", "content": "That was not valid JSON. Please return ONLY a valid JSON object with the structure specified."}
            ]
        )
        text2 = response2.content[0].text.strip()
        if text2.startswith("```"):
            text2 = text2.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        result = json.loads(text2)

    return result


def compute_score(judge_result):
    """Compute groundedness score from judge result."""
    claims = judge_result.get("claims", [])
    if not claims:
        return 1.0, [], []  # No claims = fully grounded (conversational only)

    supported = 0
    hallucinations = []
    partial = []

    for c in claims:
        verdict = c.get("verdict", "unsupported")
        if verdict == "supported":
            supported += 1
        elif verdict == "partially_supported":
            supported += 0.5
            partial.append(c["claim"])
        else:
            hallucinations.append(c["claim"])

    score = supported / len(claims) if claims else 1.0
    return score, hallucinations, partial


# --- Main eval runner ---

def run_eval(test_cases, anthropic_client, voyage_client, verbose=False):
    """Run groundedness evaluation on all test cases."""
    total_claims = 0
    total_supported = 0
    total_hallucinations = []
    results = []

    for tc in test_cases:
        game = tc["game"]
        print(f"\n{'='*60}")
        print(f"Game: {game}")
        print(f"{'='*60}")

        for question in tc["questions"]:
            print(f"\n  Q: \"{question}\"")

            # Step 1: Retrieve context
            top_chunks, context_str = retrieve_context(question, game, voyage_client)
            if not context_str:
                print(f"  [SKIP] No chunks found for {game}")
                continue

            # Step 2: Generate answer
            answer = generate_answer(question, game, context_str, anthropic_client)
            if verbose:
                print(f"  Answer: {answer[:200]}...")

            # Step 3: Judge groundedness
            judge_result = judge_groundedness(question, context_str, answer, anthropic_client)
            score, hallucinations, partial = compute_score(judge_result)

            claims = judge_result.get("claims", [])
            n_claims = len(claims)
            n_supported = sum(1 for c in claims if c["verdict"] == "supported")
            n_partial = sum(1 for c in claims if c["verdict"] == "partially_supported")
            n_unsupported = sum(1 for c in claims if c["verdict"] == "unsupported")

            total_claims += n_claims
            total_supported += n_supported + (n_partial * 0.5)
            total_hallucinations.extend(
                [{"game": game, "question": question, "claim": h} for h in hallucinations]
            )

            # Print result
            print(f"  Score: {score:.2f} ({n_supported}/{n_claims} supported"
                  f"{f', {n_partial} partial' if n_partial else ''}"
                  f"{f', {n_unsupported} unsupported' if n_unsupported else ''})")

            if hallucinations:
                for h in hallucinations:
                    print(f"  X Hallucination: \"{h}\"")
            elif partial:
                for p in partial:
                    print(f"  ~ Partial: \"{p}\"")
            else:
                print(f"  OK All claims grounded")

            if verbose:
                for c in claims:
                    marker = {"supported": "OK", "partially_supported": "~", "unsupported": "X"}[c["verdict"]]
                    print(f"    {marker} {c['claim']}")
                    if c.get("evidence"):
                        print(f"      Evidence: {c['evidence'][:100]}...")

            results.append({
                "game": game,
                "question": question,
                "score": score,
                "claims": n_claims,
                "hallucinations": hallucinations,
                "partial": partial
            })

            # Brief pause to respect rate limits
            time.sleep(1)

    # --- Summary ---
    overall_score = total_supported / total_claims if total_claims else 1.0

    print(f"\n{'='*60}")
    print(f"GROUNDEDNESS EVALUATION SUMMARY")
    print(f"{'='*60}")
    print(f"Total questions: {len(results)}")
    print(f"Total claims evaluated: {total_claims}")
    print(f"Overall groundedness: {overall_score:.1%}")

    if total_hallucinations:
        print(f"\nHallucinations found ({len(total_hallucinations)}):")
        for h in total_hallucinations:
            print(f"  - [{h['game']}] \"{h['question']}\" -> \"{h['claim']}\"")

    if overall_score >= PASS_THRESHOLD:
        print(f"\nResult: PASS ({overall_score:.1%} >= {PASS_THRESHOLD:.0%})")
        return 0
    elif overall_score >= WARN_THRESHOLD:
        print(f"\nResult: WARNING ({overall_score:.1%} >= {WARN_THRESHOLD:.0%} but < {PASS_THRESHOLD:.0%})")
        return 1
    else:
        print(f"\nResult: FAIL ({overall_score:.1%} < {WARN_THRESHOLD:.0%})")
        return 2


def main():
    parser = argparse.ArgumentParser(description="Groundedness evaluation for Merry Meeple AI")
    parser.add_argument("--game", type=str, help="Run only tests for a specific game")
    parser.add_argument("--verbose", action="store_true", help="Show full claim details")
    parser.add_argument("--test-file", default="eval_test_cases.yaml", help="Path to test cases YAML")
    args = parser.parse_args()

    # Init
    init_database()
    anthropic_client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    voyage_client = voyageai.Client(api_key=os.environ.get("VOYAGE_API_KEY"))

    # Load test cases
    with open(args.test_file, "r") as f:
        test_cases = yaml.safe_load(f)

    # Filter by game if specified
    if args.game:
        test_cases = [tc for tc in test_cases if tc["game"].lower() == args.game.lower()]
        if not test_cases:
            print(f"No test cases found for game: {args.game}")
            sys.exit(1)

    print(f"=== Groundedness Evaluation ===")
    print(f"Test cases: {sum(len(tc['questions']) for tc in test_cases)} questions across {len(test_cases)} games")
    print(f"Pass threshold: {PASS_THRESHOLD:.0%}")

    exit_code = run_eval(test_cases, anthropic_client, voyage_client, verbose=args.verbose)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
