# Merry Meeple Rules Assistant — Claude Code Context

## Project Overview

Customer-facing AI rules assistant for **The Merry Meeple**, a board game café in Crown Heights, Brooklyn. Guests select a game from a dropdown, ask rules questions in natural language, and get answers with page citations drawn from processed rulebooks. No auth, no uploads — clean Q&A only.

Deployed at: `https://merry-meeple-rules.streamlit.app`  
Repo: `merry-meeple-rules` (public GitHub)  
Target scale: ~400-title game library; AI navigation is the explicit solution to that scale.

---

## Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| Frontend | Streamlit | Customer-facing chat UI |
| Storage | SQLite (`game_library.db`) | Single-file, committed to git |
| Embeddings | Voyage AI (`voyage-3`) | Semantic search; free tier = 3 RPM |
| LLM | Claude (`claude-sonnet-4-20250514`) | Answer generation + game detection |
| Hosting | Streamlit Cloud | Free tier; auto-deploys on push |
| PDF parsing | pypdf | Text extraction from rulebook PDFs |
| Chunking | tiktoken | 500 tokens, 50 overlap |

---

## File Structure

```
/
├── app.py                    ← Customer-facing Streamlit app (main entry point)
├── browse_ui.py              ← Browse-games hub / recommender UI
├── database.py               ← SQLite layer (games, chunks, cafe library)
├── database_recs.py          ← Recommendation-flow session/prefs/ratings
├── recommender.py            ← Deterministic 3-question rec engine
├── config.py                 ← Centralized model IDs (CLAUDE_MODEL, VOYAGE_MODEL)
├── process_rulebooks.py      ← PDF ingestion (best-of-both pypdf + OCR)
├── ocr_fallback.py           ← EasyOCR wrapper with rotation-aware retry
├── rulebook_aliases.py       ← Manual filename→cafe-library title map
├── sync_menu.py              ← GSheets → SQLite menu sync
├── sync_deals.py             ← GSheets → SQLite deals sync (NOT YET BUILT)
├── sync_bgg_to_cafe_games.py ← BGG metadata sync helper
├── bulk_enrich_bgg.py        ← BGG bulk enrichment
├── onboard_game.py           ← Add a new game record
├── user_store.py             ← Phone-gate / user identity
├── admin.py                  ← Admin views
├── requirements.txt          ← Production deps
├── game_library.db           ← SQLite database (committed to git for deployment)
├── rulebooks/                ← PDF source files (NOT in git — copyright)
├── .env                      ← API keys (NOT in git)
└── docs/
    ├── README.md
    ├── SETUP.md
    ├── DEPLOYMENT.md
    ├── FILE_NAMING_CONVENTION.md
    └── STAFF_PING_IMPLEMENTATION.md
```

---

## Database Schema

```sql
-- Games table
games (id, title TEXT UNIQUE, filename, total_pages, total_chunks, processed_date)

-- Chunks table (core RAG store)
chunks (id, game_id FK, chunk_id, page_number, text, embedding BLOB, source_type)
-- source_type: 'rulebook' | 'faq' | 'errata'

-- Deduplication tracker
processed_files (id, filename TEXT UNIQUE, game_id FK, source_type, processed_date)
```

Embeddings are stored as serialized BLOBs (numpy arrays via JSON). Cosine similarity is computed in Python at query time — no vector DB.

---

## RAG Pipeline

```
User Question
    ↓
Embed with Voyage AI (input_type="query", model="voyage-3")
    ↓
Load all chunks for selected game from SQLite
    ↓
Cosine similarity (in-memory numpy)
    ↓
Top 5 chunks (TOP_K_RESULTS = 5)
    ↓
Build context string with [Page N] labels
    ↓
Send to Claude Sonnet with system prompt (rules-only, cite page numbers)
    ↓
Return answer + source page list to UI
```

---

## File Naming Convention (Critical)

Multiple PDFs can belong to one game. The filename prefix before `-` or `_` determines game grouping:

```
wingspan-rulebook.pdf  →  game: "Wingspan", source_type: rulebook
wingspan-faq.pdf       →  game: "Wingspan", source_type: faq (appended, not new game)
catan.pdf              →  game: "Catan"
```

See `FILE_NAMING_CONVENTION.md` for full spec. Violating this creates duplicate game entries in the DB.

---

## Current Game Library (6 games, as of Feb 2026)

| Game | Chunks | Notes |
|---|---|---|
| 7 Wonders Duel | 28 | |
| Azul | 10 | |
| Catan | 42 | |
| Streets | 29 | |
| Ticket to Ride | 8 | |
| Wingspan | 43 | Rulebook + FAQ combined |

Target: 400 titles. PDF ingestion is the bottleneck.

---

## Common Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Process new PDFs (run locally, NOT on Streamlit Cloud)
python process_rulebooks.py

# Sync deals from Google Sheets (run manually or on schedule)
python sync_deals.py

# Run locally
streamlit run app.py

# Run tests
python test_pdf_processing.py
```

---

## Key Constraints & Gotchas

### Rate Limiting
Voyage AI free tier = 3 RPM. `process_rulebooks.py` has a hardcoded `RATE_LIMIT_DELAY = 25` seconds between API calls. Do NOT remove this without upgrading the Voyage AI tier.

### Deployment Model
- `game_library.db` must be committed to git — Streamlit Cloud has no persistent filesystem
- PDFs are NOT committed (copyright)
- API keys go in Streamlit Cloud Secrets, not in `.env` (which is local-only)

### Staff Ping
`send_staff_ping()` in `app.py` is a **stub** — it logs to console only. Real implementation options (SendGrid email, Twilio SMS, Slack) are documented in `STAFF_PING_IMPLEMENTATION.md`. Do not assume it sends actual notifications.

### Cosine Similarity
Similarity is computed in Python (not a vector DB). This is fine for ≤50K chunks. At 400 games × ~25 chunks avg = ~10K chunks, performance is not a concern yet.

### Model versions (updated Feb 2026)
Anthropic and Voyage retire model versions periodically. All model IDs live in `config.py`, driven by env vars (`CLAUDE_MODEL`, `VOYAGE_MODEL`). Never hardcode model strings in individual files.

**When Anthropic emails a retirement notice:**
1. Pick the successor model from `docs.anthropic.com/en/docs/about-claude/models`.
2. Update `CLAUDE_MODEL` in `.env` (local) and Streamlit Cloud Secrets (production).
3. Redeploy Streamlit Cloud — no code change needed.
4. Optionally bump the default in `config.py` for future clones.

Check quarterly for deprecation notices at `docs.anthropic.com/en/docs/about-claude/model-deprecations`. If prod ever 404s on a model call, it's almost certainly a retirement.

---

## Architecture Direction (Feb 2026 pivot)

The app is being reframed around **four button-driven paths** with AI restricted to the rules Q&A surface. Chat-first / free-text intent detection is being retired outside of Rules Help.

### The four paths (button hub)

1. **Browse Games** — filterable library, search-by-name, game detail cards. "Not on the shelf?" button fires a staff ping for retrieval.
2. **Order Food/Drinks** — menu categories → cart → send to staff. Deals surface inline (verbatim `display_text`).
3. **Game Rules Help** — pick a game (or arrive with one preselected via QR), then Q&A. **This is the only AI surface.**
4. **Call Staff** — generic ping with a reason picker (Rules / Food / Other / free text). Contextual pings from paths 1–3 still exist alongside this.

### QR entry points

A single `?loc=` URL param drives session context and adjusts the intro banner. The four buttons are identical across contexts.

- `?loc=counter` → welcome banner + phone gate → hub
- `?loc=table&t=5` → session knows Table 5 → hub
- `?loc=game&g=<slug>` → deep-link into Rules Help scoped to that game (skips picker)

Stickers on each game box (`Need rules help?`) carry the game-QR URL. Table QRs and a counter QR live at their respective locations.

### Identity model — POS as source of truth

The URL says WHERE. The POS says WHO. The app cross-references at the moment of a scan. No fragile localStorage/session juggling.

- **Counter scan**: employee enters phone verbally at check-in, or customer enters on kiosk → POS party created.
- **Table scan**: query POS for "who's at Table X" → party attaches to session.
- **Game scan**: query POS for "who currently has game X checked out" → party attaches to session.

**Staff manually checks out every game to a party** when handing it over. This is a workflow commitment.

### Identity abstraction

All reads go through a small `identity` module so a real POS API replaces the SQLite reads with no changes elsewhere:

```python
identity.get_party_at_table(table_id) -> Party | None
identity.get_party_with_game(game_slug) -> Party | None
identity.get_current_checkouts(party_id) -> list[Checkout]
```

### Party / checkout schema (deferred until POS is picked)

Intended structure. Not to be built until POS choice is documented — the schema may need to align with what POS exposes.

```sql
parties(party_id, table_id, party_size, checked_in_at, ended_at)
party_members(party_member_id, party_id, phone, name)
checkouts(checkout_id, party_id, game_slug, checked_out_at, returned_at)
sessions(session_id, party_id, entry_qr, first_seen)
```

### Staff admin page (deferred until POS is picked)

A `/staff` Streamlit page with three actions (seat a party, check out a game, close out) plus a live board. Serves as our identity backend pre-POS. When POS lands this either goes read-only or is replaced. **Do not build until POS is chosen** — its shape depends on what the POS exposes.

### Phase 1 (buildable now, no POS needed)

- 4-button hub, working across all QR entry contexts
- Rules Help fully functional; game-QR deep link works
- Browse, Order, Call Staff routes send real staff pings with table# only
- QR code generation script (tables + one per game + counter)
- Staff notification wired (SendGrid or Twilio) — see `STAFF_PING_IMPLEMENTATION.md`
- Existing rec engine either folds into Browse as a secondary "Help me pick" action, or is dropped

### Phase 2 (waits on POS decision)

- Staff admin page + `parties`/`checkouts`/`party_members`/`sessions` tables
- POS API integration behind the `identity` module
- Repeat-visitor recognition, cross-session ratings, personalized recs
- Payments (still explicitly deferred per Payment Integration section)

### What's being dropped or downgraded

- Free-text intent detection at the top level (game detection, "what are you asking?" NL routing) — replaced by explicit button selection
- Claude-as-recommender NL parser — already replaced by the 3-question deterministic flow
- Chat-first framing app-wide — chat is now scoped to Rules Help only

---

## Planned Features (Not Yet Built)

Do not implement any of the following without explicit instruction. Specs below are design intent, not implementation approval.

---

### 1. Game Recommendation Engine

**Goal:** Conversational preference extraction → personalized game suggestions from the café library.

**Phased approach:**

**Phase 1 — Content-based MVP**
- Session tracking via QR code URL param (`?session=UUID`)
- Preference extraction from conversation (group size, experience level, preferred mechanics, playtime)
- Rule-based scoring against game metadata (BGG tags, complexity, player count)
- Surface 3 suggestions with brief reasoning
- Thumbs up/down rating stored per session in SQLite

**Phase 2 — Collaborative filtering**
- Item-based: "customers who played X also enjoyed Y"
- Hybrid score: content-based weight + collaborative weight
- Requires ~200+ ratings to be meaningful; don't activate until then

**Phase 3 — BGG enrichment (Phil's system)**
- Read-only access to Phil's BigQuery warehouse (~200K games, LightGBM/CatBoost models)
- Enrich café library records with BGG complexity scores, community ratings, mechanic tags
- Pull similarity vectors for "hidden gem" recommendations
- Weekly automated sync; do not call Phil's API at query time (latency)
- **Do not modify anything touching Phil's data contracts without flagging.**

**New DB tables needed:**
```sql
sessions (id, session_uuid TEXT, table_id TEXT, party_size INT, created_at TIMESTAMP)
preferences (id, session_id FK, key TEXT, value TEXT)
  -- e.g. key='experience', value='beginner'
ratings (id, session_id FK, game_id FK, rating INT, rated_at TIMESTAMP)
  -- rating: 1=thumbs up, -1=thumbs down
game_metadata (id, game_id FK, bgg_id INT, complexity FLOAT, min_players INT,
               max_players INT, min_minutes INT, max_minutes INT,
               mechanics TEXT, categories TEXT, bgg_rating FLOAT, last_synced TIMESTAMP)
```

---

### 2. Deals & Upsell System

**Goal:** Owner-controlled discount and upsell offers surfaced by the AI at contextually appropriate moments.

#### Core Enforcement Rule
The AI may **never** offer, imply, describe, or paraphrase a deal that is not pre-approved and stored in the `deals` table with `active=1`. When presenting a deal, the AI must output the `display_text` field **verbatim** — no rewording, no summarizing, no elaboration. This constraint must be hardcoded into the system prompt and is non-negotiable.

#### Architecture

**Source of truth:** Google Sheet maintained by Casey (owner).  
**Runtime source:** SQLite `deals` table in `game_library.db`.  
**Sync:** `sync_deals.py` pulls from GSheets API → upserts into SQLite. Deactivates deals removed from sheet. Run manually or on a schedule (e.g., every 15 min).

#### Deals Table Schema

```sql
CREATE TABLE deals (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id               TEXT UNIQUE NOT NULL,      -- e.g. 'HAPPY_HOUR_01'
    name                  TEXT NOT NULL,             -- internal label (not shown to customer)
    display_text          TEXT NOT NULL,             -- VERBATIM text AI outputs to customer
    discount_type         TEXT NOT NULL,             -- 'percent' | 'flat' | 'free_item'
    discount_value        REAL,                      -- 20.0 = 20%, 5.00 = $5 flat
    free_item_description TEXT,                      -- used when discount_type='free_item'
    min_spend             REAL DEFAULT 0,            -- minimum tab to qualify (0 = no min)
    min_visit_count       INTEGER DEFAULT 0,         -- prior sessions required (0 = no min)
    min_party_size        INTEGER DEFAULT 1,         -- minimum group size
    first_visit_only      INTEGER DEFAULT 0,         -- 1 = new customers only
    time_of_day_start     TEXT,                      -- 'HH:MM' 24h, NULL = no restriction
    time_of_day_end       TEXT,                      -- 'HH:MM' 24h, NULL = no restriction
    days_of_week          TEXT,                      -- 'Mon,Tue,Wed' CSV, NULL = every day
    games_played_min      INTEGER DEFAULT 0,         -- games played this session to qualify
    active                INTEGER DEFAULT 1,         -- 1 = live, 0 = disabled
    expiry_date           TEXT,                      -- 'YYYY-MM-DD', NULL = no expiry
    last_synced           TIMESTAMP
)
```

#### Google Sheet Column Headers
Must match exactly for sync script to work:
```
deal_id | name | display_text | discount_type | discount_value | free_item_description |
min_spend | min_visit_count | min_party_size | first_visit_only | time_of_day_start |
time_of_day_end | days_of_week | games_played_min | active | expiry_date
```

#### Eligibility Logic (Python, not AI)

Evaluate all `active=1`, non-expired deals against current session state. Classify each as:
- **`eligible`**: all conditions met
- **`near_miss`**: one condition off within threshold:
  - Spend: within $5.00
  - Party size: within 1 person
  - Time: within 15 minutes of window opening

Pass results to Claude as a structured context block — not free text:

```
ELIGIBLE_DEALS: [{"deal_id": "HAPPY_HOUR_01", "display_text": "<exact text>"}]
NEAR_MISS_DEALS: [{"deal_id": "PITCHER_DEAL", "gap": "spend $4.50 more", "display_text": "<exact text>"}]
```

#### AI Upsell Behavior

The AI checks for surfacing opportunities at two moments:
1. **Proactively** — when a customer mentions ordering a drink or asks about F&B
2. **On session milestone** — after game selection, after a completed rules exchange

**Permitted AI outputs:**
- ✅ `"You're $4.50 away from unlocking a deal — [exact display_text]"`
- ✅ `"Good news — you qualify for: [exact display_text]"`
- ❌ Any rewording, summarizing, or elaborating beyond `display_text`
- ❌ Mentioning any deal not passed in the context block

#### New Env Variables
```
GOOGLE_SHEETS_ID=...
GOOGLE_SERVICE_ACCOUNT_JSON=...   # path to service account key, or inline JSON
```

---

### 3. Multilingual Concierge

Claude handles multilingual Q&A natively. Primary work is language detection in the UI and translation of static copy. No separate model needed. High value for Crown Heights demographics, low implementation effort.

---

### 4. F&B Ordering Interface

Order-routing only — no payment capture at this stage. Customers select menu items in-chat; request is routed to staff via the ping system. Staff ping must be live before this is buildable.

---

### 5. Staff Ping (Live Implementation)

Replace `send_staff_ping()` stub in `app.py`. See `STAFF_PING_IMPLEMENTATION.md`. Recommended path: SendGrid email for MVP, Twilio SMS for urgent routing. Route by `reason`:
- `rules_question` → email
- `food_order`, `new_game` → SMS

---

### 6. QR Code / Table Tracking

URL param `?table=N` encoded in QR codes placed at each table. Extracted via `st.query_params` in `app.py`. Required before staff ping or deals system can be table-aware. Unblocks several other features — build early.

---

### 7. Usage Analytics / Ratings

Thumbs up/down per answer stored in SQLite. Feeds library curation decisions, recommendation engine training data, and a future staff dashboard. Build after recommendation engine DB schema is in place.

---

### 8. Payment Integration *(Phase 4+, Design TBD)*

**Intent:** Customers settle their tab from their phone without requiring staff to close them out.

**Status:** POS/payment processor not yet selected. Do not begin implementation until that decision is made and documented here.

**Known constraints:**
- Requires per-session tab tracking (cover charge + F&B orders)
- Needs reconciliation with chosen POS
- Likely candidates: Square (easiest café integration), Toast (restaurant-focused), Stripe (most developer control, no hardware dependency)
- Do NOT build custom card capture. Use hosted payment page (Stripe Checkout, Square Web Payments SDK) to stay out of PCI scope.

**When processor is chosen, fill in:**
- Processor:
- Integration approach (hosted page vs embedded):
- Tab data model:
- Reconciliation flow:

---

## Active Development Priorities

In rough priority order:
1. Scale game library toward 400 titles (PDF processing workflow)
2. QR code / table tracking (unblocks staff ping + deals)
3. Implement live staff ping (SendGrid or Twilio)
4. Deals & upsell system (`sync_deals.py` + deals table + AI prompt integration)
5. BGG metadata integration (coordinate with Phil on API contract)
6. Recommendation engine MVP

---

## Cost Profile

| Item | Rate | Monthly (200 customers × 20 questions) |
|---|---|---|
| Claude Sonnet (answer) | ~$0.011/question | ~$44 |
| Voyage AI (query embed) | ~$0.0000015/embed | ~$0.006 |
| Streamlit Cloud | Free | $0 |
| Google Sheets API | Free (service account) | $0 |
| **Total (unoptimized)** | | **~$44/month** |

Caching frequently-asked questions could reduce to ~$8/month.

---

## Environment Variables

```
ANTHROPIC_API_KEY=...              # Claude API
VOYAGE_API_KEY=...                 # Voyage AI embeddings
GOOGLE_SHEETS_ID=...               # Deals sheet (when deals system is built)
GOOGLE_SERVICE_ACCOUNT_JSON=...    # GSheets service account (when deals system is built)
```

Local: `.env` file  
Production: Streamlit Cloud Secrets (Settings → Secrets)

---

## External Collaborators

- **Phil** — Maintains BGG data warehouse in BigQuery. LightGBM/CatBoost models trained on ~200K games. Future integration target for recommendation engine Phase 3. Do not modify anything touching Phil's data contracts without flagging.
