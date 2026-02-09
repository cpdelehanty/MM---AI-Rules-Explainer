"""
Test script to validate PDF processing pipeline
Run this before starting the full Streamlit app
"""

from pypdf import PdfReader

def test_pdf_extraction():
    """Test that we can extract text from the Streets PDF"""
    print("🔍 Testing PDF extraction...")
    
    reader = PdfReader("streets_rulebook.pdf")
    total_pages = len(reader.pages)
    
    print(f"✅ Successfully loaded PDF: {total_pages} pages")
    
    # Show first page sample
    first_page_text = reader.pages[0].extract_text()
    print(f"\n📄 First page preview (first 200 chars):")
    print(first_page_text[:200])
    print("...")
    
    return total_pages

def test_chunking(total_pages):
    """Test the chunking logic (using character-based approximation)"""
    print(f"\n🔍 Testing chunking logic...")
    
    reader = PdfReader("streets_rulebook.pdf")
    
    # Approximate: ~4 chars per token, so 500 tokens ≈ 2000 chars
    chunk_size_chars = 2000
    overlap_chars = 200
    total_chunks = 0
    
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text()
        text_len = len(text)
        
        # Count chunks for this page
        start = 0
        page_chunks = 0
        while start < text_len:
            page_chunks += 1
            start += (chunk_size_chars - overlap_chars)
        
        total_chunks += page_chunks
        
        if i == 1:  # Show details for first page
            print(f"  Page {i}: {text_len} chars → {page_chunks} chunks")
    
    print(f"✅ Total: {total_pages} pages → {total_chunks} chunks")
    print(f"   (Avg: {total_chunks / total_pages:.1f} chunks/page)")
    
    return total_chunks

def test_sample_chunk():
    """Show what a sample chunk looks like"""
    print(f"\n📦 Sample chunk:")
    
    reader = PdfReader("streets_rulebook.pdf")
    
    # Get page 5 (Setup page)
    setup_page = reader.pages[4].extract_text()
    
    # Get first ~2000 chars (approximates 500 tokens)
    chunk_text = setup_page[:2000]
    
    print(f"  Page: 5 (Setup)")
    print(f"  Characters: {len(chunk_text)}")
    print(f"  Text preview:")
    print("  " + "-" * 60)
    print("  " + chunk_text[:300].replace("\n", "\n  "))
    print("  ...")

def main():
    print("=" * 70)
    print("🎲 Rulebook Assistant - PDF Processing Test")
    print("=" * 70)
    print()
    
    try:
        total_pages = test_pdf_extraction()
        total_chunks = test_chunking(total_pages)
        test_sample_chunk()
        
        print("\n" + "=" * 70)
        print("✅ All tests passed! PDF processing works correctly.")
        print("=" * 70)
        print("\nTo run the full app (requires API keys + dependencies):")
        print("\n1. Install all dependencies:")
        print("   pip install -r requirements.txt")
        print("\n2. Set your API keys:")
        print("   export ANTHROPIC_API_KEY='your-key'")
        print("   export VOYAGE_API_KEY='your-key'")
        print("\n3. Run the Streamlit app:")
        print("   streamlit run rulebook_assistant.py")
        print()
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        print("\nMake sure pypdf is installed:")
        print("   pip install pypdf")

if __name__ == "__main__":
    main()
