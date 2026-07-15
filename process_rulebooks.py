"""
Process rulebooks from /rulebooks folder
FREE TIER VERSION: Adds delays to respect 3 RPM rate limit
Run this script whenever you add new PDFs
"""

import os
import re
import sqlite3
import time
from pypdf import PdfReader
import tiktoken
import voyageai
from dotenv import load_dotenv
from database import init_database, game_exists, add_game, get_all_games, get_library_stats, file_already_processed, DB_PATH
from rulebook_aliases import ALIASES
from config import VOYAGE_MODEL

# Load environment variables
load_dotenv()

# Configuration
RULEBOOKS_FOLDER = "rulebooks"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
RATE_LIMIT_DELAY = 25  # Wait 25 seconds between API calls (free tier = 3 RPM)

def extract_game_title_from_filename(filename):
    """
    Convert filename to game title, combining related documents

    Examples:
        wingspan-rulebook.pdf → Wingspan
        wingspan-faq.pdf → Wingspan
        Wingspan - Rulebook.pdf → Wingspan
        Wingspan - FAQ.pdf → Wingspan
        catan.pdf → Catan
    """
    # Remove .pdf extension
    title = filename.replace('.pdf', '')

    # Handle different separator styles
    separators = [' - ', '-', '_']
    for sep in separators:
        if sep in title:
            # Take everything before the separator as the base game name
            title = title.split(sep)[0]
            break

    # Clean up and title case
    title = title.strip()
    title = title.replace('_', ' ').replace('-', ' ')
    title = title.title()

    return title


def _normalize(s):
    """
    Lowercase, fold accents (é -> e), strip non-alphanumerics.
    Used for fuzzy name matching where 'Orléans' should equal 'Orleans'.
    """
    import unicodedata
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def resolve_to_cafe_name(derived_title):
    """
    Map a filename-derived title to its canonical cafe_games.name.

    Resolution order:
        1. Manual alias from rulebook_aliases.ALIASES
        2. Exact case-sensitive match against cafe_games.name
        3. Case+punctuation-insensitive match (`Brass Lancashire` -> `Brass: Lancashire`)
        4. Fall through to derived_title (rulebook is not in the cafe library)

    The returned title is what gets stored as games.title, ensuring the
    rules-assistant game and the cafe_games row share the same key.
    """
    if derived_title in ALIASES:
        return ALIASES[derived_title]

    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT name FROM cafe_games")
        cafe_names = [r[0] for r in cur.fetchall()]
        conn.close()
    except sqlite3.OperationalError:
        # cafe_games table may not exist yet (e.g. fresh DB)
        return derived_title

    if derived_title in cafe_names:
        return derived_title

    norm_target = _normalize(derived_title)
    for n in cafe_names:
        if _normalize(n) == norm_target:
            return n

    return derived_title

def get_document_type(filename):
    """
    Determine document type from filename
    
    Returns: 'rulebook', 'faq', 'errata', or 'supplement'
    """
    filename_lower = filename.lower()
    
    if 'faq' in filename_lower or 'f.a.q' in filename_lower:
        return 'faq'
    elif 'errata' in filename_lower:
        return 'errata'
    elif 'rulebook' in filename_lower or 'rules' in filename_lower:
        return 'rulebook'
    else:
        return 'supplement'

_spellchecker = None


def _get_spellchecker():
    global _spellchecker
    if _spellchecker is None:
        from spellchecker import SpellChecker
        _spellchecker = SpellChecker(language="en", distance=1)
    return _spellchecker


def quality_score(text):
    """
    Heuristic quality score, 0-1+. Higher = cleaner, more usable text.

    Uses pyspellchecker to distinguish real English words from OCR garbage
    (Kuild, Houett, etc.). Penalizes:
    - Low real-word ratio (main signal)
    - Excessively long single tokens (Dominion-style no-space failures)
    """
    if not text or len(text.strip()) < 20:
        return 0.0

    tokens = [t.lower().strip(".,!?;:()[]\"'-—") for t in text.split()]
    tokens = [t for t in tokens if 2 <= len(t) <= 20 and t.replace("-", "").replace("'", "").isalpha()]
    if not tokens:
        return 0.0

    sc = _get_spellchecker()
    # SpellChecker.known(words) returns the SET of unique real English words.
    # Count occurrences (not uniques) so repeated words like "the" score correctly.
    real_set = sc.known(set(tokens))
    n_real = sum(1 for t in tokens if t in real_set)
    real_ratio = n_real / len(tokens)

    # Space-discipline check: penalize single super-long tokens
    all_tokens = text.split()
    max_token_len = max(len(t) for t in all_tokens)
    if max_token_len > 30:
        real_ratio *= 20 / max_token_len

    # Small length tiebreaker
    length_kick = min(len(text) / 2000, 0.05)
    return real_ratio + length_kick


def extract_text_from_pdf(pdf_path):
    """
    Run BOTH pypdf and EasyOCR on every page; pick the higher-scoring output
    per page. Best-of-both extraction — pypdf's clean text wins on well-formed
    PDFs, OCR wins where pypdf fails (image-based PDFs, encoding failures).
    """
    from ocr_fallback import ocr_pages_from_pdf
    reader = PdfReader(pdf_path)
    n_pages = len(reader.pages)

    print(f"  🔍 Running pypdf + OCR on all {n_pages} pages...")
    ocr_texts = ocr_pages_from_pdf(pdf_path, list(range(1, n_pages + 1)))

    pages = []
    wins = {"pypdf": 0, "ocr": 0, "tie": 0}
    for i, pypdf_page in enumerate(reader.pages, start=1):
        pypdf_text = (pypdf_page.extract_text() or "").strip()
        ocr_text = (ocr_texts.get(i, "") or "").strip()

        py_score = quality_score(pypdf_text)
        oc_score = quality_score(ocr_text)

        # Slightly prefer pypdf when scores are close (< 0.05 apart) since it
        # produces cleaner text on well-formed PDFs
        if py_score >= oc_score - 0.05:
            if py_score > oc_score:
                wins["pypdf"] += 1
            else:
                wins["tie"] += 1
            pages.append({"page": i, "text": pypdf_text})
        else:
            wins["ocr"] += 1
            pages.append({"page": i, "text": ocr_text})

    print(f"     Winners: pypdf={wins['pypdf']}  OCR={wins['ocr']}  tie(pypdf)={wins['tie']}")
    return pages

def chunk_text(pages, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Chunk text into segments"""
    encoding = tiktoken.get_encoding("cl100k_base")
    chunks = []
    
    for page_data in pages:
        page_num = page_data["page"]
        text = page_data["text"]
        tokens = encoding.encode(text)
        
        start = 0
        while start < len(tokens):
            end = start + chunk_size
            chunk_tokens = tokens[start:end]
            chunk_text = encoding.decode(chunk_tokens)
            
            chunks.append({
                "text": chunk_text,
                "page": page_num,
                "chunk_id": len(chunks)
            })
            
            start += (chunk_size - overlap)
    
    return chunks

def create_embeddings(chunks, voyage_client):
    """Generate embeddings for chunks - FREE TIER VERSION with rate limiting"""
    texts = [chunk["text"] for chunk in chunks]
    
    # Batch into groups of 10 to stay under rate limits
    batch_size = 10
    all_embeddings = []
    
    print(f"  Generating embeddings for {len(chunks)} chunks...")
    print(f"  (Processing in batches of {batch_size}, ~{RATE_LIMIT_DELAY}s delay between batches)")
    
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i+batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (len(texts) + batch_size - 1) // batch_size
        
        print(f"  Processing batch {batch_num}/{total_batches} ({len(batch)} chunks)...")
        
        try:
            result = voyage_client.embed(texts=batch, model=VOYAGE_MODEL, input_type="document")
            all_embeddings.extend(result.embeddings)
            print(f"  ✅ Batch {batch_num} complete")
            
            # Wait between batches (except for the last one)
            if i + batch_size < len(texts):
                print(f"  ⏳ Waiting {RATE_LIMIT_DELAY} seconds (rate limit)...")
                time.sleep(RATE_LIMIT_DELAY)
        
        except Exception as e:
            print(f"  ❌ Error on batch {batch_num}: {e}")
            print(f"  Retrying after {RATE_LIMIT_DELAY * 2} seconds...")
            time.sleep(RATE_LIMIT_DELAY * 2)
            
            # Retry once
            try:
                result = voyage_client.embed(texts=batch, model=VOYAGE_MODEL, input_type="document")
                all_embeddings.extend(result.embeddings)
                print(f"  ✅ Retry successful")
            except Exception as retry_error:
                print(f"  ❌ Retry failed: {retry_error}")
                raise
    
    # Add embeddings to chunks
    for i, chunk in enumerate(chunks):
        chunk["embedding"] = all_embeddings[i]
    
    return chunks

def process_pdf(pdf_path, voyage_client):
    """Process a single PDF file"""
    filename = os.path.basename(pdf_path)
    derived_title = extract_game_title_from_filename(filename)
    base_game_title = resolve_to_cafe_name(derived_title)
    doc_type = get_document_type(filename)

    print(f"\n📖 Processing: {filename}")
    if base_game_title != derived_title:
        print(f"   → Derived: {derived_title!r}")
        print(f"   → Linked to cafe_games: {base_game_title!r}")
    else:
        print(f"   → Base game: {base_game_title}")
    print(f"   → Type: {doc_type}")
    
    # Check if this exact file was already processed
    if file_already_processed(filename):
        print(f"  ⏭️  File already processed, skipping")
        return False
    
    # Check if base game exists
    existing_game = game_exists(base_game_title)
    if existing_game:
        print(f"  📚 Found existing '{base_game_title}' - will add to it")
    
    # Extract text
    print(f"  📄 Extracting text...")
    pages = extract_text_from_pdf(pdf_path)
    total_pages = len(pages)
    print(f"  ✅ Extracted {total_pages} pages")
    
    # Chunk text
    print(f"  ✂️  Chunking text...")
    chunks = chunk_text(pages)
    print(f"  ✅ Created {len(chunks)} chunks")
    
    # Generate embeddings
    chunks_with_embeddings = create_embeddings(chunks, voyage_client)
    
    # Store in database (merges with existing game if it exists)
    print(f"  💾 Storing in database...")
    add_game(base_game_title, filename, total_pages, chunks_with_embeddings, source_type=doc_type)
    
    if existing_game:
        print(f"  ✅ Successfully added to existing game!")
    else:
        print(f"  ✅ Successfully created new game!")
    
    return True

def main():
    """Main processing function"""
    print("=" * 70)
    print("🎲 RULEBOOK PROCESSOR - FREE TIER VERSION")
    print("=" * 70)
    print("⚠️  Using rate-limited processing (3 requests/min)")
    print("   This is slower but works without payment method")
    print("   Estimated time: ~2-3 minutes per game")
    print("=" * 70)
    
    # Initialize database
    print("\n🔧 Initializing database...")
    init_database()
    print("✅ Database ready")
    
    # Initialize Voyage client
    print("\n🔑 Connecting to Voyage AI...")
    api_key = os.environ.get("VOYAGE_API_KEY")
    if not api_key:
        print("❌ ERROR: VOYAGE_API_KEY not found in .env file")
        return
    
    voyage_client = voyageai.Client(api_key=api_key)
    print("✅ Connected")
    
    # Check for rulebooks folder
    if not os.path.exists(RULEBOOKS_FOLDER):
        print(f"\n❌ ERROR: '{RULEBOOKS_FOLDER}' folder not found")
        print(f"   Create a folder named '{RULEBOOKS_FOLDER}' and add your PDF files there")
        return
    
    # Find all PDFs
    pdf_files = [f for f in os.listdir(RULEBOOKS_FOLDER) if f.endswith('.pdf')]
    
    if not pdf_files:
        print(f"\n⚠️  No PDF files found in '{RULEBOOKS_FOLDER}' folder")
        return
    
    print(f"\n📚 Found {len(pdf_files)} PDF file(s)")
    
    # Process each PDF
    processed_count = 0
    skipped_count = 0
    
    for pdf_file in pdf_files:
        pdf_path = os.path.join(RULEBOOKS_FOLDER, pdf_file)
        was_processed = process_pdf(pdf_path, voyage_client)
        
        if was_processed:
            processed_count += 1
        else:
            skipped_count += 1
    
    # Show summary
    print("\n" + "=" * 70)
    print("📊 PROCESSING COMPLETE")
    print("=" * 70)
    print(f"✅ Processed: {processed_count} new game(s)")
    print(f"⏭️  Skipped: {skipped_count} (already in library)")
    
    # Show library stats
    stats = get_library_stats()
    print(f"\n📚 Library Statistics:")
    print(f"   Total games: {stats['total_games']}")
    print(f"   Total pages: {stats['total_pages']}")
    print(f"   Total chunks: {stats['total_chunks']}")
    
    # List all games
    games = get_all_games()
    if games:
        print(f"\n🎮 Games in Library:")
        for game in games:
            print(f"   • {game['title']} ({game['total_pages']} pages, {game['total_chunks']} chunks)")
    
    print("\n✨ Ready to run customer app: streamlit run app.py")
    print()

if __name__ == "__main__":
    main()
