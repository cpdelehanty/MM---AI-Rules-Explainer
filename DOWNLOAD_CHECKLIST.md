# Download Checklist - Merry Meeple Rules Assistant

## All Files to Download (10 total)

### Core Application Files (4 files)
- [ ] **app.py** - Customer chat interface (conversational)
- [ ] **database.py** - SQLite storage functions
- [ ] **process_rulebooks.py** - PDF processing script
- [ ] **requirements.txt** - Python dependencies

### Configuration Files (2 files)
- [ ] **.env.template** - Template for API keys (rename to `.env` after adding your keys)
- [ ] **.gitignore** - Protects sensitive files from Git

### Documentation Files (3 files)
- [ ] **README.md** - Project overview
- [ ] **SETUP.md** - Complete setup instructions
- [ ] **DEPLOYMENT.md** - Streamlit Cloud deployment guide

### Sample PDF (1 file)
- [ ] **streets_rulebook.pdf** - Test rulebook (Streets game)

---

## After Downloading

### 1. Create Project Folder
```
C:\Users\cpdel\merry-meeple-rules\
```

Put all 10 downloaded files in this folder.

### 2. Create Rulebooks Subfolder
```
C:\Users\cpdel\merry-meeple-rules\rulebooks\
```

### 3. Set Up Your API Keys

**Option A (Easy):**
1. Rename `.env.template` to `.env`
2. Open `.env` in Notepad
3. Replace `YOUR-KEY-HERE` with your actual keys
4. Save

**Option B (Manual):**
1. Create new file named `.env` in Notepad
2. Add these two lines:
   ```
   ANTHROPIC_API_KEY=sk-ant-api03-YOUR-ACTUAL-KEY
   VOYAGE_API_KEY=pa-YOUR-ACTUAL-KEY
   ```
3. Save

### 4. Add Rulebook PDFs

Add **5 PDFs total** to the `rulebooks/` folder:
- ✅ streets.pdf (you already have this)
- ❌ catan.pdf (you need to find)
- ❌ ticket_to_ride.pdf (you need to find)
- ❌ wingspan.pdf (you need to find)
- ❌ azul.pdf (you need to find)

**Or use any 4 other games you prefer!**

---

## Final Folder Structure Should Look Like:

```
C:\Users\cpdel\merry-meeple-rules\
├── app.py
├── database.py
├── process_rulebooks.py
├── requirements.txt
├── .env                    ← You create this from .env.template
├── .gitignore
├── README.md
├── SETUP.md
├── DEPLOYMENT.md
└── rulebooks\
    ├── streets.pdf
    ├── catan.pdf
    ├── ticket_to_ride.pdf
    ├── wingspan.pdf
    └── azul.pdf
```

---

## Next Steps

Once all files are in place:

```cmd
cd C:\Users\cpdel\merry-meeple-rules
python -m pip install -r requirements.txt
python process_rulebooks.py
python -m streamlit run app.py
```

See **SETUP.md** for detailed instructions!
