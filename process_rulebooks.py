"""
Process rulebooks from /rulebooks folder
FREE TIER VERSION: Adds delays to respect 3 RPM rate limit
Run this script whenever you add new PDFs
"""

import os
import time
from pypdf import PdfReader
import tiktoken
import voyageai
from dotenv import load_dotenv
from database import init_database, game_exists, add_game, get_all_games, get_library_stats

# Load environment variables
load_dotenv()

# Configuration
RULEBOOKS_FOLDER = "rulebooks"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
RATE_LIMIT_DELAY = 25  # Wait 25 seconds between API calls (free tier = 3 RPM)

def extract_game_title_from_filename(filename):
    """Convert filename to game title"""
    # Remove .pdf extension
    title = filename.replace('.pdf', '')
    # Replace underscores/hyphens with spaces
    title = title.replace('_', ' ').replace('-', ' ')
    # Title case
    title = title.title()
    return title

def extract_text_from_pdf(pdf_path):
    """Extract text from PDF with page numbers"""
    reader = PdfReader(pdf_path)
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text()
        pages.append({"page": i, "text": text})
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
            result = voyage_client.embed(texts=batch, model="voyage-3", input_type="document")
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
                result = voyage_client.embed(texts=batch, model="voyage-3", input_type="document")
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
    title = extract_game_title_from_filename(filename)
    
    print(f"\n📖 Processing: {title}")
    
    # Check if already processed
    if game_exists(title):
        print(f"  ⏭️  Already in library, skipping")
        return False
    
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
    
    # Store in database
    print(f"  💾 Storing in database...")
    add_game(title, filename, total_pages, chunks_with_embeddings)
    print(f"  ✅ Successfully added to library!")
    
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
