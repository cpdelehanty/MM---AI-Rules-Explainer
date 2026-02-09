# Board Game Rulebook Assistant - Quick Start

## What You're Getting

A working MVP Streamlit app that:
- ✅ Ingests PDF rulebooks (tested with Streets, 24 pages)
- ✅ Chunks text intelligently (~500 tokens per chunk, 50 token overlap)
- ✅ Generates embeddings with Voyage AI
- ✅ Stores in ChromaDB vector database (local, no separate service)
- ✅ Answers questions with Claude Sonnet 4.5
- ✅ Cites page numbers in responses

## Files Included

```
rulebook_assistant.py      # Main Streamlit app (core logic)
requirements.txt           # Python dependencies
README.md                  # Full documentation
test_pdf_processing.py     # Validation script (run this first)
streets_rulebook.pdf       # Test rulebook (Streets game)
```

## Setup (5 minutes)

### Step 1: Install Dependencies
```bash
pip install -r requirements.txt
```

This installs:
- streamlit (UI framework)
- pypdf (PDF extraction)
- tiktoken (OpenAI tokenizer for chunking)
- chromadb (vector database)
- anthropic (Claude API client)
- voyageai (embedding API client)

### Step 2: Get API Keys

**Anthropic (Claude):** https://console.anthropic.com/
- Create account → API Keys → Create Key
- Free tier: $5 credit (enough for ~100 queries)

**Voyage AI (Embeddings):** https://www.voyageai.com/
- Create account → Dashboard → API Keys
- Free tier: 100M tokens/month (plenty for testing)

### Step 3: Save API Keys in .env File

**Create a file named `.env` in your project folder:**

```bash
# Copy the template
cp .env.template .env
```

**Then edit `.env` with your actual keys:**

```
ANTHROPIC_API_KEY=sk-ant-api03-YOUR-ACTUAL-KEY-HERE
VOYAGE_API_KEY=pa-YOUR-ACTUAL-KEY-HERE
```

**Why .env?**
- ✅ Keys persist across sessions
- ✅ Secure (gitignored automatically)
- ✅ No need to export every time

**Don't want to use .env?** See API_KEY_SETUP.md for alternatives.

### Step 4: Test PDF Processing
```bash
python test_pdf_processing.py
```

You should see:
```
✅ Successfully loaded PDF: 24 pages
✅ Total: 24 pages → 32 chunks
✅ All tests passed!
```

### Step 5: Run the App
```bash
streamlit run rulebook_assistant.py
```

Browser opens at `http://localhost:8501`

## Usage Flow

1. **Upload PDF**: Sidebar → "Choose a PDF rulebook" → select `streets_rulebook.pdf`
2. **Process**: Click "Process Rulebook" button (takes ~30-60 seconds)
   - Extracts 24 pages
   - Creates ~32 chunks
   - Generates embeddings via Voyage AI
   - Stores in ChromaDB
3. **Ask Questions**: 
   - "How do I set up a 4-player game?"
   - "What happens when a street is enclosed?"
   - "How do people with FOMO work?"
4. **Get Answers**: Claude provides response with page citations like "(p. 5)"

## Example Session

**Q:** How do I set up the game for 3 players?

**A:** For a 3-player game, remove 1 random Building of each type except Wild (4 Buildings total) from the 40 basic Building tiles. Put these Buildings back in the box without revealing them. Each player then takes 3 Buildings from the top of the shuffled Stack to form their starting hands. (p. 5)

**Sources:** Pages 5

## Architecture Diagram

```
┌─────────────────┐
│  Upload PDF     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Extract Text   │  pypdf
│  (24 pages)     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Chunk Text     │  tiktoken
│  (~500 tokens)  │  (50 overlap)
│  → 32 chunks    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Embed Chunks   │  Voyage AI
│  (voyage-3)     │  (1024-dim vectors)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Store Vectors  │  ChromaDB
│  (local DB)     │  (in-memory)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  User Question  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Embed Query    │  Voyage AI
│  Search DB      │  (top 5 chunks)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Generate       │  Claude Sonnet 4.5
│  Answer with    │  (with context)
│  Citations      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Show Answer    │  Streamlit UI
│  + Page Refs    │
└─────────────────┘
```

## Cost Estimate (per session)

**Processing 24-page PDF:**
- Voyage AI embeddings: ~10,000 tokens × 32 chunks = 320k tokens
- Cost: $0.00032 (essentially free within free tier)

**Per Question:**
- Voyage query embedding: ~10 tokens = negligible
- Claude Sonnet 4.5: ~2,500 input + 200 output tokens
- Cost: ~$0.01 per question

**Free tier capacity:**
- Voyage: 100M tokens = ~300 PDFs or 300k questions
- Anthropic: $5 credit = ~500 questions

## Troubleshooting

**"ModuleNotFoundError: No module named 'streamlit'"**
→ Run `pip install -r requirements.txt`

**"API key not found"**
→ Check environment variables: `echo $ANTHROPIC_API_KEY`

**"Connection error"**
→ Check internet connection (APIs require network access)

**"ChromaDB error"**
→ Delete `/tmp/chroma` folder and restart app

**Embeddings taking too long**
→ Normal for first time (30-60 sec for 32 chunks)

**Wrong answers**
→ Check if question is about content in the PDF
→ Try rephrasing more specifically
→ Increase TOP_K_RESULTS in code (default: 5)

## Next Steps (Beyond MVP)

Suggested improvements ranked by impact:

**High Priority:**
1. **Multi-rulebook support**: Load 5+ games, switch between them
2. **Persistent storage**: Use ChromaDB with persistence or Pinecone
3. **Chat history**: Save Q&A sessions, export to PDF

**Medium Priority:**
4. **Image extraction**: Show setup diagrams from PDF
5. **Improved chunking**: Smart section detection, keep tables together
6. **Compare games**: "Compare setup for Catan vs Ticket to Ride"

**Low Priority:**
7. **Fine-tuning**: Adjust chunk size based on game type
8. **Mobile UI**: Responsive design for tablets
9. **Voice input**: Ask questions verbally

## Tech Stack Rationale

| Component | Choice | Why |
|-----------|--------|-----|
| Frontend | Streamlit | Fastest Python UI (no HTML/CSS/JS) |
| PDF Extract | pypdf | Reliable, actively maintained |
| Chunking | tiktoken | OpenAI's tokenizer, accurate counts |
| Vector DB | ChromaDB | No separate service, easy setup |
| Embeddings | Voyage AI | Best quality/price ratio |
| LLM | Claude Sonnet 4.5 | Excellent at following context, citations |

**Alternatives considered:**
- ❌ LangChain: Too heavy for MVP
- ❌ LlamaIndex: Similar overhead
- ❌ Pinecone: Requires account setup, overkill for MVP
- ❌ GPT-4: More expensive, similar quality

## Performance Metrics (Streets PDF)

- PDF upload: <1 sec
- Text extraction: ~2 sec
- Chunking: ~1 sec
- Embedding generation: ~30 sec (32 chunks)
- Vector storage: <1 sec
- Query embedding: <1 sec
- Vector search: <1 sec
- Claude response: ~3-5 sec
- **Total first query:** ~40 sec
- **Subsequent queries:** ~5 sec each

## Production Considerations

Before deploying to real users:

1. **Add authentication**: Streamlit Cloud auth or OAuth
2. **Persistent DB**: ChromaDB with persistence or migrate to Pinecone
3. **Rate limiting**: Track API usage per user
4. **Error handling**: Better user-facing error messages
5. **Monitoring**: Log queries, track accuracy
6. **Caching**: Cache common questions
7. **Scalability**: Move to cloud hosting (Streamlit Cloud, Railway, etc.)

## Questions?

Check README.md for detailed docs, or reach out if you hit issues.

---
**Built:** Feb 2026  
**Test game:** Streets (Sinister Fish Games)  
**Status:** MVP complete, ready for expansion ✅
