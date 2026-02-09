# Merry Meeple Rules Assistant - Setup Guide

## What You're Building

**Customer experience:**
1. Open app on tablet/phone
2. Select game from dropdown
3. Ask rules questions
4. Get instant answers with page citations

**Your workflow:**
1. Drop PDFs in `rulebooks/` folder
2. Run processing script once
3. Deploy to Streamlit Cloud
4. Customers can access from any device

---

## Quick Start (5 Steps)

### 1. Get Your 5 PDFs Ready

Create a folder structure:
```
merry-meeple-rules/
└── rulebooks/
    ├── streets.pdf
    ├── catan.pdf
    ├── ticket_to_ride.pdf
    ├── wingspan.pdf
    └── azul.pdf
```

**Note:** PDF filenames become game titles:
- `streets.pdf` → "Streets"
- `ticket_to_ride.pdf` → "Ticket To Ride"
- `7_wonders_duel.pdf` → "7 Wonders Duel"

---

### 2. Download All Project Files

Put these files in your `merry-meeple-rules/` folder:
- `app.py` (customer interface)
- `database.py` (storage)
- `process_rulebooks.py` (processing script)
- `requirements.txt` (dependencies)
- `.env` (your API keys)
- `.gitignore` (security)

Your folder should look like:
```
merry-meeple-rules/
├── app.py
├── database.py
├── process_rulebooks.py
├── requirements.txt
├── .env
├── .gitignore
└── rulebooks/
    └── (your 5 PDFs)
```

---

### 3. Install Dependencies

```bash
cd merry-meeple-rules
python -m pip install -r requirements.txt
```

---

### 4. Process Your Rulebooks

```bash
python process_rulebooks.py
```

**You'll see:**
```
🎲 RULEBOOK PROCESSOR
====================================
🔧 Initializing database...
✅ Database ready

🔑 Connecting to Voyage AI...
✅ Connected

📚 Found 5 PDF file(s)

📖 Processing: Streets
  📄 Extracting text...
  ✅ Extracted 24 pages
  ✂️  Chunking text...
  ✅ Created 32 chunks
  Generating embeddings for 32 chunks...
  💾 Storing in database...
  ✅ Successfully added to library!

... (repeats for each game)

📊 PROCESSING COMPLETE
====================================
✅ Processed: 5 new game(s)

📚 Library Statistics:
   Total games: 5
   Total pages: 103
   Total chunks: 142

🎮 Games in Library:
   • Streets (24 pages, 32 chunks)
   • Catan (20 pages, 28 chunks)
   • Ticket To Ride (16 pages, 22 chunks)
   • Wingspan (25 pages, 34 chunks)
   • Azul (18 pages, 26 chunks)

✨ Ready to run customer app: streamlit run app.py
```

**This creates:** `game_library.db` (your processed game library)

**Time:** ~2-3 minutes for 5 games

**Cost:** ~$0.002 (negligible)

---

### 5. Test Locally

```bash
python -m streamlit run app.py
```

Browser opens to `http://localhost:8501`

**Try it:**
1. Select "Streets" from dropdown
2. Ask: "How do I set up for 3 players?"
3. Get answer with page citations

**Works? Deploy to Streamlit Cloud!** (See DEPLOYMENT.md)

---

## File Descriptions

### Customer-Facing Files:

**app.py** - Main customer interface
- Game selection dropdown
- Question input
- Answer display
- Mobile-friendly UI

**game_library.db** - Processed rulebook database
- Created by `process_rulebooks.py`
- Contains all game text + embeddings
- SQLite format (single file)

---

### Backend Files:

**database.py** - Database functions
- Creates/manages SQLite database
- Stores/retrieves game chunks
- Handles embeddings

**process_rulebooks.py** - Processing script
- Scans `rulebooks/` folder
- Extracts text from PDFs
- Chunks text intelligently
- Generates embeddings (Voyage AI)
- Stores in database

---

### Configuration Files:

**requirements.txt** - Python dependencies
- Lists all packages needed
- Pip installs from this

**.env** - API keys (not committed to Git)
```
ANTHROPIC_API_KEY=sk-ant-...
VOYAGE_API_KEY=pa-...
```

**.gitignore** - Protects secrets
- Excludes .env from Git
- Allows game_library.db (needed for deployment)

---

## Adding New Games

**Anytime you get a new game:**

```bash
# 1. Add PDF to folder
cp new_game.pdf rulebooks/

# 2. Process it
python process_rulebooks.py
# (Only processes new PDFs, skips existing)

# 3. Test locally
python -m streamlit run app.py

# 4. Deploy (if using Streamlit Cloud)
git add game_library.db rulebooks/new_game.pdf
git commit -m "Added new_game"
git push
```

**Streamlit Cloud auto-updates when you push to GitHub.**

---

## Removing Games

**To remove a game from library:**

```python
# In Python console:
from database import delete_game
delete_game("Game Title")
```

Or delete `game_library.db` and reprocess all PDFs.

---

## Managing the Library

**View all games:**
```bash
python -c "from database import get_all_games; import json; print(json.dumps(get_all_games(), indent=2))"
```

**Get stats:**
```bash
python -c "from database import get_library_stats; import json; print(json.dumps(get_library_stats(), indent=2))"
```

---

## Cost Breakdown

### One-Time Setup (Processing 5 Games):
- Voyage AI embeddings: ~$0.002
- **Total: Less than 1 cent**

### Per Customer Question:
- Voyage query embedding: ~$0.0000015
- Claude Sonnet answer: ~$0.011
- **Total: ~1 cent per question**

### Monthly (estimated for 200 customers, 20 questions each):
- 4,000 questions × $0.011 = **$44/month**
- With caching: **~$20-25/month**

### Hosting:
- Streamlit Cloud: **Free**
- Custom domain (optional): **$20/month** (Streamlit Pro)

---

## Troubleshooting

### "VOYAGE_API_KEY not found"
→ Create `.env` file with your API keys
→ Format: `VOYAGE_API_KEY=pa-YOUR-KEY` (no quotes)

### "No PDF files found in 'rulebooks' folder"
→ Create `rulebooks/` folder in project root
→ Add your PDF files there

### Processing fails on a specific PDF
→ PDF might be image-based (scanned)
→ Try a different PDF or use OCR first
→ Check PDF isn't password-protected

### "No games in library yet"
→ Run `python process_rulebooks.py` first
→ Check that `game_library.db` was created

### Customer app shows wrong game
→ Clear Streamlit cache: Press 'C' then 'Enter' in running app
→ Or restart: Stop app (Ctrl+C) and rerun

### Database is locked
→ Close any other programs accessing `game_library.db`
→ Only one process can write to SQLite at a time

---

## Development Tips

### Testing Changes Without Reprocessing:

**If you only change `app.py`:**
- No need to rerun `process_rulebooks.py`
- Just restart Streamlit
- Database stays intact

**If you change `database.py` or processing logic:**
- Delete `game_library.db`
- Rerun `process_rulebooks.py`

### Faster Processing for Development:

Create `rulebooks_test/` with 1-2 small PDFs, then:
```bash
# Edit process_rulebooks.py, change line:
RULEBOOKS_FOLDER = "rulebooks_test"

# Process quickly
python process_rulebooks.py

# Test
python -m streamlit run app.py
```

---

## Next Steps

1. ✅ Process your 5 PDFs locally (done when you see library stats)
2. ✅ Test the customer app (verify answers are accurate)
3. ✅ Deploy to Streamlit Cloud (see DEPLOYMENT.md)
4. ✅ Share URL with staff for testing
5. ✅ Create QR code for cafe tablets
6. ✅ Add more games as needed

**Questions? Issues? Let me know!**
