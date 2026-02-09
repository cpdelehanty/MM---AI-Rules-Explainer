# File Naming Convention - Quick Guide

## ✅ Your Approved Naming Convention

**All files for the same game should start with the same base name:**

```
wingspan-rulebook.pdf
wingspan-faq.pdf
wingspan-errata.pdf

OR

Wingspan - Rulebook.pdf
Wingspan - FAQ.pdf
Wingspan - Errata.pdf
```

Both styles work! Use whichever you prefer.

---

## How It Works

### **Processing (Behind the Scenes)**

When you run `python process_rulebooks.py`:

1. **Finds:**
   - `wingspan-rulebook.pdf`
   - `wingspan-faq.pdf`

2. **Extracts base name:** Both → `"Wingspan"`

3. **Stores in database:**
   - Creates ONE game entry: "Wingspan"
   - Adds rulebook chunks with source_type='rulebook'
   - Adds FAQ chunks with source_type='faq'
   - Total: One game with combined chunks

4. **Customer sees:** Just "Wingspan" (not "Wingspan Rulebook" and "Wingspan FAQ" separately)

---

### **Searching (Customer Experience)**

Customer selects "Wingspan" and asks question:

1. **AI searches:** ALL chunks tagged with "Wingspan"
   - ✅ Rulebook chunks
   - ✅ FAQ chunks  
   - ✅ Errata chunks

2. **AI cites sources:**
   ```
   "Each player draws 4 cards"
   📄 Pages: 5, 12
   📚 Sources: 📖 Rulebook + ❓ FAQ
   ```

3. **Customer gets:** Best answer from all available sources

---

## Naming Examples

### ✅ CORRECT

```
wingspan-rulebook.pdf           → Base: "Wingspan"
wingspan-faq.pdf                → Base: "Wingspan"
Wingspan - Rulebook.pdf         → Base: "Wingspan"
Wingspan - FAQ.pdf              → Base: "Wingspan"
catan-rules.pdf                 → Base: "Catan"
catan-seafarers-expansion.pdf   → Base: "Catan"
Ticket to Ride - Rulebook.pdf   → Base: "Ticket To Ride"
Ticket to Ride - FAQ.pdf        → Base: "Ticket To Ride"
```

**Result:** Files with matching base names merge into one game

---

### ❌ INCORRECT

```
wingspan_rulebook.pdf           → Base: "Wingspan Rulebook"
wingspan_faq.pdf                → Base: "Wingspan Faq"
```

**Problem:** Underscore (`_`) treated as space → creates TWO separate games

**Fix:** Use hyphen (`-`) or ` - ` (space-hyphen-space) as separator

---

### ⚠️ EDGE CASES

```
wingspan.pdf                    → Base: "Wingspan"
wingspan-rulebook.pdf           → Base: "Wingspan"
```

**Result:** BOTH merge into "Wingspan" ✅

```
Wingspan-Rulebook.pdf           → Base: "Wingspan"
wingspan-FAQ.pdf                → Base: "Wingspan"
```

**Result:** Case-insensitive, both merge ✅

```
wingspan-oceania-expansion.pdf  → Base: "Wingspan"
wingspan-oceania-faq.pdf        → Base: "Wingspan"
```

**Result:** Everything before first `-` is the base name ✅

---

## Recognized Document Types

The system auto-detects document types from filenames:

| Keyword in Filename | Detected Type | Icon |
|---------------------|---------------|------|
| `rulebook`, `rules` | rulebook | 📖 |
| `faq`, `f.a.q` | faq | ❓ |
| `errata` | errata | ⚠️ |
| (anything else) | supplement | 📑 |

**Examples:**
- `wingspan-rulebook.pdf` → rulebook
- `wingspan-official-faq.pdf` → faq
- `wingspan-clarifications.pdf` → supplement

---

## What Happens to Old Files?

### If You Already Processed Files

**Old way:**
```
streets.pdf           → Game: "Streets"
catan.pdf             → Game: "Catan"
```

**These still work!** No need to rename existing files.

---

### Adding FAQs to Existing Games

**Scenario:** You already have `catan.pdf` processed.

**Add FAQ:**
1. Download official FAQ
2. Name it: `catan-faq.pdf`
3. Put in `rulebooks/` folder
4. Run: `python process_rulebooks.py`

**Result:**
```
📖 Processing: catan-faq.pdf
   → Base game: Catan
   → Type: faq
📚 Found existing 'Catan' - will add to it
✅ Successfully added to existing game!
```

**Customer sees:** One "Catan" game with rulebook + FAQ chunks

---

## File Organization

### Recommended Folder Structure

```
rulebooks/
├── wingspan-rulebook.pdf
├── wingspan-faq.pdf
├── wingspan-errata.pdf
├── catan-rulebook.pdf
├── catan-faq.pdf
├── ticket-to-ride-rulebook.pdf
├── ticket-to-ride-faq.pdf
└── streets-rulebook.pdf
```

**Clean, organized, easy to add new documents!**

---

## Processing Workflow

### Initial Setup (5 games)
```bash
# Put all files in rulebooks/
rulebooks/
├── wingspan-rulebook.pdf
├── catan-rulebook.pdf
├── ticket-to-ride-rulebook.pdf
├── streets-rulebook.pdf
└── azul-rulebook.pdf

# Process them
python process_rulebooks.py

# Result: 5 games in database
```

---

### Adding FAQs Later
```bash
# Download official FAQs
# Name them with matching base names
rulebooks/
├── wingspan-rulebook.pdf        (already processed)
├── wingspan-faq.pdf              (NEW!)
├── catan-rulebook.pdf            (already processed)
└── catan-faq.pdf                 (NEW!)

# Process again
python process_rulebooks.py

# Output:
📖 Processing: wingspan-faq.pdf
   → Base game: Wingspan
   → Type: faq
📚 Found existing 'Wingspan' - will add to it
✅ Successfully added to existing game!

# Result: Wingspan now has rulebook + FAQ chunks
```

---

## Database Structure

### Before (Old Way)
```
Games:
- Streets (32 chunks from streets.pdf)
- Catan (45 chunks from catan.pdf)
```

### After (With Naming Convention)
```
Games:
- Streets (32 chunks: 32 rulebook)
- Catan (60 chunks: 45 rulebook + 15 faq)
- Wingspan (80 chunks: 60 rulebook + 15 faq + 5 errata)
```

**Customer searches "Catan" → gets results from all 60 chunks!**

---

## Testing

### Verify It Works

**After processing multiple files:**

```bash
python process_rulebooks.py
```

**Look for output like:**
```
📖 Processing: wingspan-rulebook.pdf
   → Base game: Wingspan
   → Type: rulebook
✅ Successfully created new game!

📖 Processing: wingspan-faq.pdf
   → Base game: Wingspan
   → Type: faq
📚 Found existing 'Wingspan' - will add to it
✅ Successfully added to existing game!

📊 PROCESSING COMPLETE
📚 Library Statistics:
   Total games: 1
   Total pages: 48
   Total chunks: 75

🎮 Games in Library:
   • Wingspan (48 pages, 75 chunks)
```

**One game, multiple sources!** ✅

---

## Customer Experience

### What Customers See

**Game List:**
```
🎮 Available Games:
- Wingspan
- Catan
- Ticket to Ride
- Streets
- Azul
```

**Clean, simple! No "Wingspan FAQ" or "Catan Errata" cluttering the list.**

---

### When They Ask Questions

**Example 1: Answer from rulebook only**
```
Customer: "I'm playing Wingspan"
AI: "Got it! Wingspan is a bird collection game..."

Customer: "How many players?"
AI: "Wingspan supports 1-5 players (p. 1)"
📄 Pages: 1
```

---

**Example 2: Answer from multiple sources**
```
Customer: "What about the nectar tokens?"
AI: "Nectar tokens are a new resource added in the Oceania expansion.
They can be spent as any food type... (pp. 2-3, FAQ p. 1)"
📄 Pages: 2, 3, 8
📚 Sources: 📖 Rulebook + ❓ FAQ
```

**Customer sees which sources contributed to the answer!**

---

## Migration Guide

### Already Have Games Processed?

**Don't worry!** Old games still work.

**To add FAQs to existing games:**

1. **Check current game names:**
   ```bash
   python -c "from database import get_all_games; print([g['title'] for g in get_all_games()])"
   ```
   
   Output: `['Streets', 'Catan', 'Wingspan']`

2. **Name FAQs to match:**
   ```
   streets-faq.pdf    → Matches "Streets"
   catan-faq.pdf      → Matches "Catan"
   wingspan-faq.pdf   → Matches "Wingspan"
   ```

3. **Process:**
   ```bash
   python process_rulebooks.py
   ```

4. **Result:** FAQs added to existing games ✅

---

## Quick Reference

| Your File Name | Base Game Name | Document Type |
|----------------|----------------|---------------|
| `wingspan-rulebook.pdf` | Wingspan | Rulebook |
| `wingspan-faq.pdf` | Wingspan | FAQ |
| `Wingspan - Errata.pdf` | Wingspan | Errata |
| `catan-rules.pdf` | Catan | Rulebook |
| `Catan - FAQ.pdf` | Catan | FAQ |
| `azul.pdf` | Azul | Rulebook |

**All files with same base name → merged into one game!**

---

## Benefits

✅ **Clean UI:** Customer sees one game, not "Game", "Game FAQ", "Game Errata"  
✅ **Better answers:** AI searches all sources automatically  
✅ **Easy maintenance:** Just add `gamename-faq.pdf` to add FAQ  
✅ **Source transparency:** Customer sees which docs were used  
✅ **Flexible:** Works with any separator style you prefer  

---

## Questions?

**Q: Can I mix separators?**  
A: Yes! `wingspan-rulebook.pdf` and `Wingspan - FAQ.pdf` both merge into "Wingspan"

**Q: What if FAQ has different name than rulebook?**  
A: Just rename to match base name. `wingspan_official_faq.pdf` → `wingspan-faq.pdf`

**Q: Do I need to reprocess rulebooks to add FAQs?**  
A: No! Just add the FAQ with matching base name and process.

**Q: Can I have multiple FAQs?**  
A: Yes! `wingspan-faq-1.pdf`, `wingspan-faq-2.pdf` both merge into "Wingspan"

**Q: What about expansions?**  
A: Use same base name: `wingspan-oceania.pdf` → merges with "Wingspan"

---

**Keep it simple: [Game Name]-[Doc Type].pdf** ✅
