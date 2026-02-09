"""
Board Game Rulebook Assistant - MVP
Ingests PDF rulebooks and answers setup/rules questions with citations
"""

import streamlit as st
import os
from pypdf import PdfReader
import tiktoken
import chromadb
from anthropic import Anthropic
import voyageai

# Load environment variables from .env file if it exists
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed, will use system env vars

# Configuration
CHUNK_SIZE = 500  # tokens
CHUNK_OVERLAP = 50  # tokens
TOP_K_RESULTS = 5  # number of chunks to retrieve

# Initialize clients
@st.cache_resource
def init_clients():
    """Initialize API clients"""
    anthropic_client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    voyage_client = voyageai.Client(api_key=os.environ.get("VOYAGE_API_KEY"))
    chroma_client = chromadb.Client()
    return anthropic_client, voyage_client, chroma_client

# PDF Processing Functions
def extract_text_from_pdf(pdf_path):
    """Extract text from PDF with page numbers"""
    reader = PdfReader(pdf_path)
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text()
        pages.append({"page": i, "text": text})
    return pages

def chunk_text(pages, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Chunk text into ~500 token segments with 50 token overlap"""
    encoding = tiktoken.get_encoding("cl100k_base")
    chunks = []
    
    for page_data in pages:
        page_num = page_data["page"]
        text = page_data["text"]
        
        # Tokenize the page
        tokens = encoding.encode(text)
        
        # Create overlapping chunks
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
            
            # Move forward by (chunk_size - overlap)
            start += (chunk_size - overlap)
    
    return chunks

def create_embeddings(chunks, voyage_client):
    """Generate embeddings for all chunks"""
    texts = [chunk["text"] for chunk in chunks]
    
    # Voyage AI batch embedding
    result = voyage_client.embed(
        texts=texts,
        model="voyage-3",
        input_type="document"
    )
    
    embeddings = result.embeddings
    
    # Add embeddings to chunks
    for i, chunk in enumerate(chunks):
        chunk["embedding"] = embeddings[i]
    
    return chunks

def store_in_chroma(chunks, collection_name, chroma_client):
    """Store chunks in ChromaDB"""
    # Create or get collection
    try:
        collection = chroma_client.get_collection(name=collection_name)
        chroma_client.delete_collection(name=collection_name)
    except:
        pass
    
    collection = chroma_client.create_collection(
        name=collection_name,
        metadata={"description": "Board game rulebook chunks"}
    )
    
    # Prepare data for insertion
    ids = [f"chunk_{chunk['chunk_id']}" for chunk in chunks]
    documents = [chunk["text"] for chunk in chunks]
    embeddings = [chunk["embedding"] for chunk in chunks]
    metadatas = [{"page": chunk["page"]} for chunk in chunks]
    
    # Add to collection
    collection.add(
        ids=ids,
        documents=documents,
        embeddings=embeddings,
        metadatas=metadatas
    )
    
    return collection

# Query Functions
def query_rulebook(question, collection, voyage_client, anthropic_client, top_k=TOP_K_RESULTS):
    """Query the rulebook and generate an answer"""
    
    # Embed the question
    question_embedding = voyage_client.embed(
        texts=[question],
        model="voyage-3",
        input_type="query"
    ).embeddings[0]
    
    # Query ChromaDB
    results = collection.query(
        query_embeddings=[question_embedding],
        n_results=top_k
    )
    
    # Build context from retrieved chunks
    context_parts = []
    for i, (doc, metadata) in enumerate(zip(results['documents'][0], results['metadatas'][0])):
        page = metadata['page']
        context_parts.append(f"[Page {page}]\n{doc}")
    
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
    
    # Also return the source pages for transparency
    source_pages = sorted(set([m['page'] for m in results['metadatas'][0]]))
    
    return answer, source_pages

# Streamlit UI
def main():
    st.set_page_config(page_title="Rulebook Assistant", page_icon="🎲", layout="wide")
    
    st.title("🎲 Board Game Rulebook Assistant")
    st.markdown("Upload a rulebook PDF and ask setup or rules questions!")
    
    # Initialize clients
    anthropic_client, voyage_client, chroma_client = init_clients()
    
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
            
            # Save the uploaded file temporarily
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
                
                with st.spinner("Storing in vector database..."):
                    collection = store_in_chroma(
                        chunks_with_embeddings,
                        "rulebook_collection",
                        chroma_client
                    )
                    st.session_state['collection'] = collection
                
                st.success("✅ Rulebook processed successfully!")
                st.info(f"📄 {st.session_state['total_pages']} pages → {st.session_state['total_chunks']} chunks")
        
        # Stats
        if 'total_pages' in st.session_state:
            st.markdown("---")
            st.metric("Pages", st.session_state['total_pages'])
            st.metric("Chunks", st.session_state['total_chunks'])
    
    # Main area for Q&A
    if 'collection' not in st.session_state:
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
                    st.session_state['collection'],
                    voyage_client,
                    anthropic_client
                )
            
            # Display answer
            st.markdown("### Answer")
            st.markdown(answer)
            
            # Display sources
            st.markdown(f"**📖 Sources:** Pages {', '.join(map(str, source_pages))}")
        
        # Chat history (optional enhancement)
        if 'chat_history' not in st.session_state:
            st.session_state['chat_history'] = []
        
        if question and st.session_state.get('last_question') != question:
            st.session_state['last_question'] = question

if __name__ == "__main__":
    main()
