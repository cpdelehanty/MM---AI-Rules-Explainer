"""
Board Game Rulebook Assistant - Simplified (No ChromaDB)
Works on Windows without C++ compilation
"""

import streamlit as st
import os
import numpy as np
from pypdf import PdfReader
import tiktoken
from anthropic import Anthropic
import voyageai
import pickle

# Load environment variables from .env file if it exists
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Configuration
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
TOP_K_RESULTS = 5

# Initialize clients
@st.cache_resource
def init_clients():
    """Initialize API clients"""
    anthropic_client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    voyage_client = voyageai.Client(api_key=os.environ.get("VOYAGE_API_KEY"))
    return anthropic_client, voyage_client

# PDF Processing
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
    """Generate embeddings for chunks"""
    texts = [chunk["text"] for chunk in chunks]
    result = voyage_client.embed(texts=texts, model="voyage-3", input_type="document")
    embeddings = result.embeddings
    
    for i, chunk in enumerate(chunks):
        chunk["embedding"] = embeddings[i]
    
    return chunks

def cosine_similarity(vec1, vec2):
    """Calculate cosine similarity between two vectors"""
    vec1 = np.array(vec1)
    vec2 = np.array(vec2)
    return np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))

def search_chunks(query_embedding, chunks, top_k=TOP_K_RESULTS):
    """Search for most similar chunks"""
    similarities = []
    for chunk in chunks:
        sim = cosine_similarity(query_embedding, chunk["embedding"])
        similarities.append((sim, chunk))
    
    # Sort by similarity (highest first)
    similarities.sort(reverse=True, key=lambda x: x[0])
    
    # Return top K
    return [chunk for _, chunk in similarities[:top_k]]

def query_rulebook(question, chunks, voyage_client, anthropic_client, top_k=TOP_K_RESULTS):
    """Query rulebook and generate answer"""
    
    # Embed the question
    question_embedding = voyage_client.embed(
        texts=[question],
        model="voyage-3",
        input_type="query"
    ).embeddings[0]
    
    # Search for similar chunks
    top_chunks = search_chunks(question_embedding, chunks, top_k)
    
    # Build context
    context_parts = []
    for chunk in top_chunks:
        page = chunk['page']
        context_parts.append(f"[Page {page}]\n{chunk['text']}")
    
    context = "\n\n---\n\n".join(context_parts)
    
    # Generate answer with Claude
    prompt = f"""You are a board game rules expert. Answer the user's question based ONLY on the rulebook excerpts provided below. 

If the answer is in the rulebook, provide a clear, direct answer and cite the relevant page number(s) in the format: (p. X) or (pp. X-Y).

If the answer is not in the provided excerpts, say "I don't see that information in the rulebook sections I found."

RULEBOOK EXCERPTS:
{context}

USER QUESTION: {question}

YOUR ANSWER:"""

    message = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    answer = message.content[0].text
    source_pages = sorted(set([chunk['page'] for chunk in top_chunks]))
    
    return answer, source_pages

# Streamlit UI
def main():
    st.set_page_config(page_title="Rulebook Assistant", page_icon="🎲", layout="wide")
    
    st.title("🎲 Board Game Rulebook Assistant")
    st.markdown("Upload a rulebook PDF and ask setup or rules questions!")
    
    # Initialize clients
    anthropic_client, voyage_client = init_clients()
    
    # Sidebar for PDF upload
    with st.sidebar:
        st.header("📁 Rulebook Upload")
        
        uploaded_file = st.file_uploader(
            "Choose a PDF rulebook",
            type="pdf",
            help="Upload a board game rulebook to get started"
        )
        
        if uploaded_file is not None:
            st.success(f"Loaded: {uploaded_file.name}")
            
            # Save temporarily
            pdf_path = f"/tmp/{uploaded_file.name}"
            with open(pdf_path, "wb") as f:
                f.write(uploaded_file.read())
            
            # Process button
            if st.button("🔄 Process Rulebook", type="primary"):
                with st.spinner("Extracting text from PDF..."):
                    pages = extract_text_from_pdf(pdf_path)
                    st.session_state['total_pages'] = len(pages)
                
                with st.spinner("Chunking text..."):
                    chunks = chunk_text(pages)
                    st.session_state['total_chunks'] = len(chunks)
                
                with st.spinner("Generating embeddings..."):
                    chunks_with_embeddings = create_embeddings(chunks, voyage_client)
                    st.session_state['chunks'] = chunks_with_embeddings
                
                st.success("✅ Rulebook processed successfully!")
                st.info(f"📄 {st.session_state['total_pages']} pages → {st.session_state['total_chunks']} chunks")
        
        # Stats
        if 'total_pages' in st.session_state:
            st.markdown("---")
            st.metric("Pages", st.session_state['total_pages'])
            st.metric("Chunks", st.session_state['total_chunks'])
    
    # Main Q&A area
    if 'chunks' not in st.session_state:
        st.info("👈 Upload and process a rulebook to get started!")
    else:
        st.header("💬 Ask a Question")
        
        # Example questions
        with st.expander("📝 Example Questions"):
            st.markdown("""
            - How do I set up a 4-player game?
            - What happens when a street is enclosed?
            - How do people with FOMO work?
            - When can I abandon a building?
            - How is end-game scoring calculated?
            """)
        
        # Question input
        question = st.text_input(
            "Your question:",
            placeholder="e.g., How do I set up the game for 3 players?",
            label_visibility="collapsed"
        )
        
        if st.button("🔍 Get Answer", type="primary") and question:
            with st.spinner("Searching rulebook..."):
                answer, source_pages = query_rulebook(
                    question,
                    st.session_state['chunks'],
                    voyage_client,
                    anthropic_client
                )
            
            # Display answer
            st.markdown("### Answer")
            st.markdown(answer)
            
            # Display sources
            st.markdown(f"**📖 Sources:** Pages {', '.join(map(str, source_pages))}")

if __name__ == "__main__":
    main()
