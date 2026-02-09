# Merry Meeple Rules Assistant

AI-powered board game rules assistant for The Merry Meeple cafe.

## What This Is

Customer-facing chat interface where guests can:
- Select their game from a dropdown
- Ask any rules or setup question
- Get instant answers with page citations from the actual rulebook

**No uploads, no complexity** - just clean Q&A.

---

## Quick Start

See **SETUP.md** for detailed instructions.

### TL;DR:

```bash
# 1. Add your PDFs
mkdir rulebooks
# (copy your 5 PDFs into rulebooks/)

# 2. Install dependencies
pip install -r requirements.txt

# 3. Process rulebooks
python process_rulebooks.py

# 4. Run app
streamlit run app.py
```

---

## Files

- **app.py** - Customer-facing chat interface
- **process_rulebooks.py** - Batch process PDFs (run once per new game)
- **database.py** - SQLite storage layer
- **requirements.txt** - Python dependencies
- **SETUP.md** - Complete setup instructions
- **DEPLOYMENT.md** - Streamlit Cloud deployment guide

---

## Architecture

```
Customer opens app
    ↓
Selects game from dropdown (pre-loaded from database)
    ↓
Asks question: "How do I set up for 4 players?"
    ↓
App embeds question (Voyage AI)
    ↓
Searches database for relevant rulebook sections
    ↓
Sends context + question to Claude Sonnet
    ↓
Claude generates answer with page citations
    ↓
Customer sees: "For 4 players, place the Central Station... (p. 5)"
```

---

## Tech Stack

- **Frontend:** Streamlit (Python web framework)
- **Storage:** SQLite (single-file database)
- **Embeddings:** Voyage AI (semantic search)
- **LLM:** Claude Sonnet 4.5 (answer generation)
- **Hosting:** Streamlit Cloud (free, browser-accessible)

---

## Cost

**Setup:** ~$0.002 (one-time, for 5 games)  
**Per question:** ~$0.011 (1 cent)  
**Monthly (200 customers × 20 questions):** ~$44  
**Hosting:** Free (Streamlit Cloud)

---

## Adding Games

```bash
# Add PDF to folder
cp new_game.pdf rulebooks/

# Process it
python process_rulebooks.py
# (Only processes new files, skips existing)

# Deploy (if using Streamlit Cloud)
git add game_library.db rulebooks/new_game.pdf
git commit -m "Added new_game"
git push
```

---

## Deployment

See **DEPLOYMENT.md** for full Streamlit Cloud setup.

**Result:** Public URL like `https://merry-meeple-rules.streamlit.app`

Works on:
- ✅ Cafe tablets
- ✅ Customer phones
- ✅ Any browser

---

## Next Steps

1. Process your 5 PDFs
2. Test locally
3. Deploy to Streamlit Cloud
4. Share URL with staff
5. Add QR code in cafe
6. Add more games as needed

---

Built for The Merry Meeple 🎲
