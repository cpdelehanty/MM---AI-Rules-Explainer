"""
Customer-Facing Rules Assistant
Conversational chat interface with natural game selection
"""

import streamlit as st
import os
import numpy as np
from anthropic import Anthropic
import voyageai
from dotenv import load_dotenv
from database import init_database, get_all_games, get_game_chunks

# Load environment variables
load_dotenv()

# Configuration
TOP_K_RESULTS = 5

# Page config
st.set_page_config(
    page_title="The Merry Meeple - Rules Assistant",
    page_icon="🎲",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# Initialize clients
@st.cache_resource
def init_clients():
    """Initialize API clients"""
    anthropic_client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    voyage_client = voyageai.Client(api_key=os.environ.get("VOYAGE_API_KEY"))
    return anthropic_client, voyage_client

# Load game library
@st.cache_data
def load_game_library():
    """Load available games from database"""
    init_database()
    games = get_all_games()
    return {game['title']: game for game in games}

# Detect which game the user is asking about
def detect_game(message, available_games, anthropic_client):
    """Use Claude to detect which game the user is referring to"""
    game_list = ", ".join(available_games)
    
    prompt = f"""The user is at a board game cafe. They just said: "{message}"

Available games: {game_list}

Which game are they referring to? Respond with ONLY the exact game title from the list, or "NONE" if they haven't mentioned a specific game yet.

Examples:
User: "We're playing Catan" → Catan
User: "I need help with Wingspan setup" → Wingspan
User: "How does Streets work?" → Streets
User: "What games do you have?" → NONE
User: "Can I get a coffee?" → NONE

Game title:"""

    response = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=50,
        messages=[{"role": "user", "content": prompt}]
    )
    
    detected = response.content[0].text.strip()
    
    # Validate it's in our list
    if detected in available_games:
        return detected
    return None

# Vector search
def cosine_similarity(vec1, vec2):
    """Calculate cosine similarity"""
    vec1 = np.array(vec1)
    vec2 = np.array(vec2)
    return np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))

def search_chunks(query_embedding, chunks, top_k=TOP_K_RESULTS):
    """Find most relevant chunks"""
    similarities = []
    for chunk in chunks:
        sim = cosine_similarity(query_embedding, chunk["embedding"])
        similarities.append((sim, chunk))
    
    similarities.sort(reverse=True, key=lambda x: x[0])
    return [chunk for _, chunk in similarities[:top_k]]

def answer_question(question, game_title, voyage_client, anthropic_client):
    """Generate answer to rules question"""
    
    # Load game chunks from database
    chunks = get_game_chunks(game_title)
    
    if not chunks:
        return "Sorry, I couldn't find the rulebook for this game in my library.", []
    
    # Embed question
    question_embedding = voyage_client.embed(
        texts=[question],
        model="voyage-3",
        input_type="query"
    ).embeddings[0]
    
    # Find relevant chunks
    top_chunks = search_chunks(question_embedding, chunks)
    
    # Build context
    context_parts = []
    for chunk in top_chunks:
        page = chunk['page']
        context_parts.append(f"[Page {page}]\n{chunk['text']}")
    
    context = "\n\n---\n\n".join(context_parts)
    
    # Detect if this is a setup question
    setup_keywords = ["setup", "set up", "start", "beginning", "prepare", "how to play", "getting started"]
    is_setup_question = any(keyword in question.lower() for keyword in setup_keywords)
    
    # Build prompt based on question type
    if is_setup_question:
        instruction = """This is a SETUP question. Provide a complete, step-by-step walkthrough of the setup process. 
- Use numbered steps
- Be thorough and detailed
- Include all components that need to be placed
- Mention player-specific setup (what each player gets/does)
- Cover any special setup for different player counts if mentioned"""
    else:
        instruction = """Provide a clear, direct answer to the specific question asked."""
    
    # Generate answer
    prompt = f"""You are a helpful board game rules assistant at The Merry Meeple cafe. Answer the customer's question based ONLY on the rulebook excerpts provided below.

{instruction}

Rules for answering:
- Be friendly and conversational
- Always cite page numbers in the format: (p. X) or (pp. X-Y)
- If the answer isn't in the excerpts, say "I don't see that specific information in the rulebook. Let me check with staff for you."
- If the question is unclear, ask ONE clarifying question
- Never make up rules that aren't in the rulebook

RULEBOOK EXCERPTS FOR {game_title.upper()}:
{context}

CUSTOMER QUESTION: {question}

YOUR ANSWER:"""

    message = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1200,  # Increased for detailed setup instructions
        messages=[{"role": "user", "content": prompt}]
    )
    
    answer = message.content[0].text
    source_pages = sorted(set([chunk['page'] for chunk in top_chunks]))
    
    return answer, source_pages

def generate_game_intro(game_title, voyage_client, anthropic_client):
    """Generate a brief intro about the game from rulebook"""
    
    # Load game chunks from database
    chunks = get_game_chunks(game_title)
    
    if not chunks:
        return f"Great! Let's dive into **{game_title}**. What would you like to know?"
    
    # Get first few chunks (usually contain overview/intro)
    intro_chunks = chunks[:5]
    context_parts = [chunk['text'] for chunk in intro_chunks]
    context = "\n\n".join(context_parts)
    
    # Generate intro
    prompt = f"""Based on the rulebook intro below, give a warm, brief 2-3 sentence welcome message about {game_title}. Mention:
- What kind of game it is (theme/genre)
- Player count
- Very brief goal/objective

Keep it conversational and inviting. Don't cite page numbers.

RULEBOOK INTRO:
{context}

Your welcome message:"""

    message = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}]
    )
    
    intro = message.content[0].text
    return f"Got it! I'm here to help with **{game_title}**.\n\n{intro}\n\nWhat would you like to know?"

def generate_general_response(message, available_games, anthropic_client):
    """Generate response when no game is selected"""
    game_list = "\n".join([f"• {game}" for game in sorted(available_games)])
    
    prompt = f"""You are a friendly board game rules assistant at The Merry Meeple cafe. The customer just said: "{message}"

They haven't selected a game yet. Respond naturally and helpfully. If they're asking about games, mention we have these available:
{game_list}

Keep your response brief (1-2 sentences) and invite them to tell you which game they're playing.

Your response:"""

    response = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}]
    )
    
    return response.content[0].text
    """Generate response when no game is selected"""
    game_list = "\n".join([f"• {game}" for game in sorted(available_games)])
    
    prompt = f"""You are a friendly board game rules assistant at The Merry Meeple cafe. The customer just said: "{message}"

They haven't selected a game yet. Respond naturally and helpfully. If they're asking about games, mention we have these available:
{game_list}

Keep your response brief (1-2 sentences) and invite them to tell you which game they're playing.

Your response:"""

    response = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}]
    )
    
    return response.content[0].text

# Main app
def main():
    # Header
    st.title("🎲 Rules Assistant")
    st.markdown("*Hey! I'm here to help with any game rules. Which game are you playing?*")
    
    # Initialize
    anthropic_client, voyage_client = init_clients()
    game_library = load_game_library()
    
    # Check if library is empty
    if not game_library:
        st.error("📚 No games in library yet!")
        st.info("Staff: Run `python process_rulebooks.py` to add games.")
        return
    
    # Initialize session state
    if 'messages' not in st.session_state:
        st.session_state.messages = []
    if 'current_game' not in st.session_state:
        st.session_state.current_game = None
    
    # Show current game if selected
    if st.session_state.current_game:
        st.info(f"🎮 Currently helping with: **{st.session_state.current_game}**")
    
    # Display chat history
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if "pages" in message and message["pages"]:
                st.caption(f"📄 Pages: {', '.join(map(str, message['pages']))}")
    
    # Chat input
    if prompt := st.chat_input("Ask about the rules, or tell me which game you're playing..."):
        # Add user message to chat
        st.session_state.messages.append({"role": "user", "content": prompt})
        
        with st.chat_message("user"):
            st.markdown(prompt)
        
        # Check if user wants to switch games
        switch_phrases = ["switch to", "change to", "let's play", "we're playing", "now playing", "actually", "instead"]
        is_switching_game = any(phrase in prompt.lower() for phrase in switch_phrases)
        
        # Only detect game if: no current game OR user is explicitly switching
        should_detect_game = (st.session_state.current_game is None) or is_switching_game
        
        if should_detect_game:
            detected_game = detect_game(prompt, list(game_library.keys()), anthropic_client)
            
            if detected_game and detected_game != st.session_state.current_game:
                # Game detected and it's different - switch to it
                st.session_state.current_game = detected_game
                
                # Generate game intro
                with st.chat_message("assistant"):
                    with st.spinner("Loading game info..."):
                        intro_message = generate_game_intro(
                            detected_game,
                            voyage_client,
                            anthropic_client
                        )
                    st.markdown(intro_message)
                
                st.session_state.messages.append({"role": "assistant", "content": intro_message})
                st.rerun()
            
            elif detected_game and detected_game == st.session_state.current_game:
                # Same game detected - just answer the question
                with st.chat_message("assistant"):
                    with st.spinner("Checking the rulebook..."):
                        answer, pages = answer_question(
                            prompt,
                            st.session_state.current_game,
                            voyage_client,
                            anthropic_client
                        )
                    st.markdown(answer)
                    if pages:
                        st.caption(f"📄 Pages: {', '.join(map(str, pages))}")
                
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": answer,
                    "pages": pages
                })
            
            else:
                # No game detected - general response
                with st.chat_message("assistant"):
                    response = generate_general_response(
                        prompt,
                        list(game_library.keys()),
                        anthropic_client
                    )
                    st.markdown(response)
                
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": response
                })
        
        else:
            # Game already selected and user isn't switching - answer about current game
            with st.chat_message("assistant"):
                with st.spinner("Checking the rulebook..."):
                    answer, pages = answer_question(
                        prompt,
                        st.session_state.current_game,
                        voyage_client,
                        anthropic_client
                    )
                st.markdown(answer)
                if pages:
                    st.caption(f"📄 Pages: {', '.join(map(str, pages))}")
            
            st.session_state.messages.append({
                "role": "assistant",
                "content": answer,
                "pages": pages
            })
    
    # Footer
    st.markdown("---")
    st.caption("💡 Just tell me which game you're playing, then ask away!")
    st.caption("🔄 You can switch games anytime by saying \"I'm playing [game name]\"")

if __name__ == "__main__":
    main()
