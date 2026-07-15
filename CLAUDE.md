# Merry Meeple Rules Assistant — Claude Code Context

## Project Overview

Customer-facing AI rules assistant for **The Merry Meeple**, a board game café in Crown Heights, Brooklyn. Every game box carries a QR code; scanning it deep-links a customer straight into a game-scoped chat that answers rules questions with page-cited answers from ingested rulebooks.

- Deployed at: `https://merry-meeple.streamlit.app`
- Repo: `merry-meeple-rules` (public GitHub, master auto-deploys)
- Corpus: **333 games / 11k chunks / 406 PDFs** (as of Jul 2026)
- Target scale: ~500 titles

The rules assistant is the **only AI surface** at Merry Meeple. Menu/deals/orders/browse/recommendation flows all lived in this app at one point; they've since been ripped out (see Architecture below).

---

## Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| Frontend | Streamlit | Single-file chat UI (`app.py`) |
| Storage | SQLite (`game_library.db`) | Committed to git; ~70MB with the full corpus |
| Embeddings | Voyage AI (`voyage-3`) | Semantic search over 1024-dim vectors; free tier = 3 RPM |
| LLM | Claude (env-driven, currently `claude-sonnet-4-5`) | Answer generation |
| Hosting | Streamlit Cloud | Free tier; auto-deploys on push to master |
| PDF parsing | pypdf + EasyOCR | Best-of-both per-page extraction |
| Chunking | tiktoken | 500 tokens, 50 overlap |

---

## File Structure

```
/
├── app.py                     ← QR-scoped rules chat (Streamlit)
├── config.py                  ← Env-driven model IDs (CLAUDE_MODEL, VOYAGE_MODEL)
├── database.py                ← 4-table SQLite layer
├── process_rulebooks.py       ← PDF → chunks → embeddings pipeline
├── ocr_fallback.py            ← EasyOCR wrapper with rotation-aware retry
├── rulebook_aliases.py        ← Manual filename → cafe-title map (edge cases)
│
├── migrate_embeddings_to_binary.py  ← One-shot JSON→float32 embedding migration
├── re_ocr_low_quality.py            ← Re-OCR pages that scored below quality threshold
├── find_problem_games.py            ← Flag low-quality/short/messy ingest results
├── extraction_full_report.py        ← Per-game pypdf/OCR/quality breakdown
├── find_dupe_sources.py             ← Detect duplicate faq/errata pairs
├── clean_dupe_sources.py            ← Apply the dupe cleanup
├── rulebook_sanity_check.py         ← Look for broken/foreign/garbled PDFs
├── clear_and_rescan.py              ← Wipe chunks + re-run full ingest
├── gather_rulebooks.py              ← Fetch missing PDFs (external sources)
│
├── rules_pipeline.py         ← Standalone RAG helper (used by eval scripts)
├── eval_groundedness.py      ← Groundedness eval harness
├── groundedness_test.py      ← Groundedness runner (unused since golden pivot)
├── test_pdf_processing.py    ← Quick smoke test for PDF ingest
├── tests/                    ← pytest suite (conftest.py + eval_rag.py)
│
├── requirements.txt           ← Production deps
├── game_library.db            ← Ingested corpus (in git for Streamlit Cloud)
├── rulebooks/                 ← PDF sources (NOT in git — copyright)
├── .env                       ← API keys (NOT in git)
└── docs/                      ← README, SETUP, DEPLOYMENT, FILE_NAMING_CONVENTION,
                                  STAFF_PING_IMPLEMENTATION
```

---

## Database Schema

Four tables only:

```sql
games (id, title UNIQUE, filename, total_pages, total_chunks, processed_date)

chunks (id, game_id FK, chunk_id, page_number, text,
        embedding BLOB, source_type)
-- source_type: 'rulebook' | 'faq' | 'errata' | 'supplement'
-- embedding: raw float32 bytes (numpy tobytes / frombuffer)

processed_files (id, filename UNIQUE, game_id FK, source_type, processed_date)

staff_requests (id, visit_id, phone, table_number, game_title, question,
                reason, status, created_at, acknowledged_at)
```

Embeddings are stored as **raw float32 little-endian bytes** (~4KB per 1024-dim vector). This shrunk the DB from 260MB → 70MB and keeps us under GitHub's 100MB per-file limit as the library grows. Cosine similarity is computed in Python at query time.

---

## RAG Pipeline

```
QR: /?g=wingspan
    ↓
app.py loads Wingspan chunks (in-memory cached per game)
    ↓
User asks a rules question
    ↓
Embed question with Voyage AI (input_type="query")
    ↓
Cosine similarity vs all Wingspan chunks (numpy)
    ↓
Top 5 chunks (TOP_K_RESULTS = 5)
    ↓
Build context with [SourceType - Page N] labels
    ↓
Stream Claude response (rules-only, cite source + page)
```

---

## URL Parameters

The QR code sticker on each game box carries the game's slug. Table stickers optionally carry table number.

| Param | Example | Effect |
|---|---|---|
| `g` | `?g=wingspan` | Scope chat to Wingspan; skip picker |
| `t` | `?t=5` | Attach table 5 for staff-ping routing |

Slug format: title lowercased, punctuation → hyphens (`"Ticket to Ride: Rails and Sails"` → `ticket-to-ride-rails-and-sails`). See `title_to_slug()` in `app.py`.

No `?g=` → testing game-picker page. Above the chat is a game-switcher dropdown for dev convenience; keep it visible until we decide to hide behind a flag in prod.

---

## File Naming Convention (Critical for Ingestion)

Multiple PDFs can belong to one game. The filename prefix before `-` or `_` determines game grouping:

```
wingspan-rulebook.pdf  →  Wingspan, source_type=rulebook
wingspan-faq.pdf       →  Wingspan, source_type=faq (appended, not a new game)
wingspan-errata.pdf    →  Wingspan, source_type=errata
catan.pdf              →  Catan, source_type=rulebook
```

See `docs/FILE_NAMING_CONVENTION.md` for the full spec. Violating this creates duplicate game entries.

---

## Best-of-Both Extraction

`process_rulebooks.py` runs BOTH pypdf (text-layer) and EasyOCR (image-based) on every page, scores each output with a pyspellchecker-driven quality heuristic, and picks the winner. Numbers from the full corpus:

- pypdf won: 72% of pages (clean text-layer PDFs)
- OCR won: 24% of pages (scans + rendered PDFs)
- Mixed: 4% of pages (close-scored, either was fine)
- Page-weighted avg quality: **0.98**

`ocr_fallback.py` handles smart rotation: if a page OCRs to garbage at 0°, it retries 90°/180°/270° and keeps the best result. Boggle page 2 (rotated in the source) is the canonical case.

---

## Common Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the app locally
streamlit run app.py

# Ingest new PDFs (drop them in rulebooks/ first)
python process_rulebooks.py

# Quality checks after ingestion
python find_problem_games.py       # low-quality / short / messy pages
python extraction_full_report.py   # per-game pypdf/OCR/quality breakdown

# Full re-ingest (wipes chunks + re-runs everything, ~24 hrs at Voyage free-tier)
python clear_and_rescan.py
```

---

## Key Constraints & Gotchas

### Rate Limiting
Voyage AI free tier = 3 RPM. `process_rulebooks.py` sleeps `RATE_LIMIT_DELAY = 25` seconds between embedding batches. Don't remove this without upgrading the Voyage tier.

### Deployment Model
- `game_library.db` **is committed to git** — Streamlit Cloud has no persistent filesystem.
- Binary-embedding format keeps the DB under GitHub's 100MB per-file cap (currently ~70MB).
- PDFs are NOT committed (copyright). Ingestion happens locally; only the resulting DB ships.
- API keys go in Streamlit Cloud Secrets, not the local `.env`.

### Model versions
All model IDs live in `config.py`, driven by env vars (`CLAUDE_MODEL`, `VOYAGE_MODEL`). Never hardcode a model string.

When Anthropic emails a retirement notice:
1. Pick the successor from `docs.anthropic.com/en/docs/about-claude/models`.
2. Update `CLAUDE_MODEL` in `.env` (local) and Streamlit Cloud Secrets (production).
3. Redeploy — no code change needed.

If prod ever 404s on a model call, it's almost certainly a retirement.

### Staff Ping
`send_staff_ping()` in `app.py` writes to the `staff_requests` table. There is **no notification wiring yet** — no email, no SMS. A future staff admin surface will poll the table. See `docs/STAFF_PING_IMPLEMENTATION.md` for the SendGrid/Twilio options when we're ready.

### Cosine Similarity Scale
Cosine is computed in Python (numpy dot product). Fine up to ~50K chunks. At 500 games × ~35 chunks avg ≈ ~18K chunks, we're comfortably in the linear-scan regime.

---

## Architecture Direction (Jul 2026 rip-down)

Everything customer-facing that wasn't rules Q&A has been stripped. What's left:

- QR on each game box → `/?g=<slug>` → chat scoped to that game.
- Optional `?t=<n>` on table stickers attaches table number for staff-ping routing.
- Contextual "📞 Get staff help" button inside the chat writes to `staff_requests`.

**Explicitly dropped:**
- 4-button hub (Browse / Order / Rules / Call Staff)
- Menu, deals, cart, orders, cart-upsells (all `sync_*` modules)
- Phone-number gate + user preferences store
- Recommender engine + BGG cafe_games sync
- Free-text intent detection / game auto-detection at the top level
- `admin.py` staff dashboard

**Identity moves to POS.** When we integrate a POS, the URL says WHERE and the POS says WHO — phone→party→table mapping lives there, not in this app.

---

## Cost Profile

| Item | Rate | Monthly (200 customers × 20 Qs) |
|---|---|---|
| Claude Sonnet 4.5 (answer) | ~$0.011/question | ~$44 |
| Voyage AI (query embed) | ~$0.0000015/embed | ~$0.006 |
| Streamlit Cloud | Free | $0 |
| **Total** | | **~$44/month** |

If costs matter at scale: swapping `CLAUDE_MODEL` to Haiku 4.5 in Streamlit Cloud Secrets drops per-question cost ~14× with the same behavioral guarantees. No code change needed.

---

## Environment Variables

```
ANTHROPIC_API_KEY=...    # Claude API
VOYAGE_API_KEY=...       # Voyage AI embeddings
CLAUDE_MODEL=claude-sonnet-4-5  # optional; config.py has the default
VOYAGE_MODEL=voyage-3           # optional; config.py has the default
```

Local: `.env`
Production: Streamlit Cloud Secrets (Settings → Secrets)

---

## External Collaborators

- **Phil** — Maintains a BGG data warehouse in BigQuery (LightGBM/CatBoost over ~200K games). Future integration target if we ever revive game recommendations. Do not touch anything hitting Phil's data contracts without flagging.
