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
from sync_menu import should_sync, sync_menu_from_sheets, format_menu_for_prompt

# Load environment variables
load_dotenv()

# Configuration
TOP_K_RESULTS = 5

def send_staff_ping(table_id, game_title, question, reason="rules_question"):
    """
    Send notification to staff (STUB - to be implemented)
    
    Args:
        table_id: Table number/identifier
        game_title: Current game being played
        question: Customer's question
        reason: Type of request (rules_question, new_game, food_order, general_help)
    
    Returns:
        dict with success status and message
    
    TODO: Implement actual notification:
    - Option 1: Email to staff (SendGrid, AWS SES)
    - Option 2: SMS to on-duty staff (Twilio)
    - Option 3: Push to staff dashboard (WebSocket/polling)
    - Option 4: Slack notification to #cafe-assistance channel
    """
    # STUB: For now, just log and return success
    print(f"[STAFF PING] Table: {table_id}, Game: {game_title}, Reason: {reason}")
    print(f"[STAFF PING] Question: {question}")
    
    # TODO: Replace with actual implementation
    # Example future implementations:
    
    # Email:
    # send_email(
    #     to="staff@merrymeeple.com",
    #     subject=f"Customer Assistance Needed - Table {table_id}",
    #     body=f"Game: {game_title}\nQuestion: {question}"
    # )
    
    # SMS:
    # twilio_client.messages.create(
    #     to="+1234567890",
    #     from_="+0987654321",
    #     body=f"Table {table_id} needs help with {game_title}: {question[:100]}"
    # )
    
    # Database:
    # db.staff_requests.insert({
    #     'timestamp': datetime.now(),
    #     'table_id': table_id,
    #     'game': game_title,
    #     'question': question,
    #     'reason': reason,
    #     'status': 'pending'
    # })
    
    return {
        "success": True,
        "message": "Staff notified! Someone will be with you shortly."
    }

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

# Load menu (syncs from Google Sheets once per day)
@st.cache_data(ttl=86400)
def load_menu():
    """Load menu, syncing from Google Sheets if needed"""
    if should_sync():
        sync_menu_from_sheets()
    return format_menu_for_prompt()

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

def answer_question(question, game_title, voyage_client, anthropic_client, menu_context=""):
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
    sources_used = set()
    for chunk in top_chunks:
        page = chunk['page']
        source_type = chunk.get('source_type', 'rulebook')
        sources_used.add(source_type)
        
        # Add source label to context
        source_label = {
            'rulebook': 'Rulebook',
            'faq': 'FAQ',
            'errata': 'Errata',
            'supplement': 'Supplement'
        }.get(source_type, 'Rulebook')
        
        context_parts.append(f"[{source_label} - Page {page}]\n{chunk['text']}")
    
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
    prompt = f"""You are a helpful board game rules assistant at The Merry Meeple cafe. Answer the customer's question based ONLY on the source documents provided below.

The sources may include:
- Rulebook (official game rules)
- FAQ (official frequently asked questions)
- Errata (official corrections/clarifications)
- Supplements (other official materials)

{instruction}

Rules for answering:
- Be friendly and conversational
- When citing information, include BOTH the source type AND page number
  Example: "According to the FAQ, nectar tokens can be spent as wild food (FAQ p. 2)"
  Example: "The rulebook states each player draws 5 cards (Rulebook p. 3)"
- If information comes from multiple sources, cite all: "This is covered in both the Rulebook (p. 5) and clarified in the FAQ (p. 2)"
- If the answer isn't in any of the provided sources, say "I don't see that information in the materials I have access to. Would you like me to request staff assistance?"
- If the customer responds with just "yes" or "yes please" after you've offered staff assistance, remind them: "Please click the '📞 Yes, get help' button above to notify staff. I can't send the notification through chat messages."
- If the question is unclear, ask ONE clarifying question
- Never make up rules that aren't in the source documents
- NEVER say you've notified staff unless the customer clicked the actual button

SOURCE DOCUMENTS FOR {game_title.upper()}:
{context}

MENU & FOOD/DRINK INFORMATION:
{menu_context}

If the customer asks about food, drinks, the menu, or retail items, answer from the MENU section above.
When the customer asks for the full menu or what's available, LIST the actual items with names and prices — do not just summarize categories.
If the customer says they want to order or is ready to order, respond helpfully and end your response with the exact tag: [STAFF_PING:food_order]
Do NOT take or confirm specific orders — just acknowledge and say staff will come by.
Do NOT invent menu items that are not listed above.
Do NOT roleplay physical actions (e.g. "slides over menu", "hands you a card"). You are a text-based assistant, not a person in the room.

CUSTOMER QUESTION: {question}

YOUR ANSWER:"""

    message = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,  # Increased for full menu listings
        messages=[{"role": "user", "content": prompt}]
    )
    
    answer = message.content[0].text
    source_pages = sorted(set([chunk['page'] for chunk in top_chunks]))
    
    # Return metadata about sources used
    return answer, source_pages, sources_used

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

def generate_general_response(message, available_games, anthropic_client, menu_context=""):
    """Generate response when no game is selected"""
    game_list = "\n".join([f"• {game}" for game in sorted(available_games)])

    prompt = f"""You are a friendly assistant at The Merry Meeple, a board game cafe in Crown Heights, Brooklyn. You can help customers with:
- Browsing the game library and recommending games based on group size, experience, or preferences
- Explaining game rules and answering rules questions
- Showing the food & drink menu and recommending items
- Sharing any active deals or discounts
- Getting a staff member for orders or any other help

The customer just said: "{message}"

Respond naturally and helpfully. If they're asking about games, here's our full library:
{game_list}

MENU & FOOD/DRINK INFORMATION:
{menu_context}

If the customer asks about food, drinks, the menu, or retail items, answer from the MENU section above.
When the customer asks for the full menu or what's available, LIST the actual items with names and prices — do not just summarize categories.
If the customer says they want to order or is ready to order, respond helpfully and end your response with the exact tag: [STAFF_PING:food_order]
Do NOT take or confirm specific orders — just acknowledge and say staff will come by.
Do NOT invent menu items that are not listed above.
Do NOT roleplay physical actions (e.g. "slides over menu", "hands you a card"). You are a text-based assistant, not a person in the room.

If the customer is asking about the menu, give a full answer. Otherwise keep your response brief (1-3 sentences).
End with a helpful "What else can I help with?" rather than always pushing them to pick a game.

Your response:"""

    response = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].text

# Main app
def main():
    # Header
    st.title("🎲 The Merry Meeple")
    st.markdown("*Your game night assistant — browse our game library, learn the rules, check out the menu, and more.*")

    # Initialize
    anthropic_client, voyage_client = init_clients()
    game_library = load_game_library()
    menu_context = load_menu()

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
    if 'pending_staff_request' not in st.session_state:
        st.session_state.pending_staff_request = None
    if 'last_question' not in st.session_state:
        st.session_state.last_question = None

    # Quick-action buttons (only show when no messages yet)
    if not st.session_state.messages:
        cols = st.columns(4)
        with cols[0]:
            if st.button("🎮 Browse games", use_container_width=True):
                st.session_state.messages.append({"role": "user", "content": "What games do you have?"})
                st.rerun()
        with cols[1]:
            if st.button("📖 Rules help", use_container_width=True):
                st.session_state.messages.append({"role": "user", "content": "I need help with game rules"})
                st.rerun()
        with cols[2]:
            if st.button("🍽️ See the menu", use_container_width=True):
                st.session_state.messages.append({"role": "user", "content": "What's on the menu?"})
                st.rerun()
        with cols[3]:
            if st.button("🙋 Get staff help", use_container_width=True):
                st.session_state.messages.append({"role": "user", "content": "I need help from a staff member"})
                st.rerun()
    
    # Show current game if selected
    if st.session_state.current_game:
        st.info(f"🎮 Currently helping with: **{st.session_state.current_game}**")
    
    # Display chat history
    for idx, message in enumerate(st.session_state.messages):
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if "pages" in message and message["pages"]:
                st.caption(f"📄 Pages: {', '.join(map(str, message['pages']))}")
            
            # Check if this message offers staff assistance
            if message["role"] == "assistant" and "request staff assistance" in message["content"].lower():
                # Show staff request button only if not already requested for this message
                if message.get("staff_requested") != True:
                    col1, col2, col3 = st.columns([1, 1, 3])
                    with col1:
                        if st.button("📞 Yes, get help", key=f"staff_yes_{idx}"):
                            # Send staff ping
                            result = send_staff_ping(
                                table_id="Unknown",  # TODO: Get from session/login
                                game_title=st.session_state.current_game or "Unknown",
                                question=st.session_state.last_question or "Help requested",
                                reason="rules_question"
                            )
                            
                            # Mark as requested
                            st.session_state.messages[idx]["staff_requested"] = True
                            
                            # Add confirmation message
                            st.session_state.messages.append({
                                "role": "assistant",
                                "content": "✅ " + result["message"]
                            })
                            st.rerun()
                    
                    with col2:
                        if st.button("📖 Check manual", key=f"staff_no_{idx}"):
                            st.session_state.messages[idx]["staff_requested"] = "declined"
                            st.session_state.messages.append({
                                "role": "assistant",
                                "content": "No problem! The physical rulebook at your table might have more details, or feel free to wave down a staff member anytime."
                            })
                            st.rerun()
                
                elif message.get("staff_requested") == True:
                    st.success("✅ Staff has been notified")
    
    # Chat input
    if prompt := st.chat_input("Ask about rules, the menu, or anything else..."):
        # Store the question for potential staff ping
        st.session_state.last_question = prompt
        
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
                        answer, pages, sources_used = answer_question(
                            prompt,
                            st.session_state.current_game,
                            voyage_client,
                            anthropic_client,
                            menu_context
                        )
                    # Check for food order staff ping
                    if "[STAFF_PING:food_order]" in answer:
                        answer = answer.replace("[STAFF_PING:food_order]", "").strip()
                        send_staff_ping(
                            table_id="Unknown",
                            game_title=st.session_state.current_game or "N/A",
                            question="Customer ready to order food/drinks",
                            reason="food_order"
                        )
                    # Store metadata for display
                    st.session_state.last_answer_meta = {'sources_used': sources_used}

                    st.markdown(answer)
                    if pages:
                        st.caption(f"📄 Pages: {', '.join(map(str, pages))}")

                    # Show source types if multiple document types were used
                    if len(sources_used) > 1:
                        source_labels = {'rulebook': '📖 Rulebook', 'faq': '❓ FAQ', 'errata': '⚠️ Errata', 'supplement': '📑 Supplement'}
                        source_str = ' + '.join([source_labels.get(s, s.title()) for s in sorted(sources_used)])
                        st.caption(f"📚 Sources: {source_str}")

                st.session_state.messages.append({
                    "role": "assistant",
                    "content": answer,
                    "pages": pages
                })

                # If answer offers staff assistance, rerun to show buttons immediately
                if "request staff assistance" in answer.lower():
                    st.rerun()

            else:
                # No game detected - general response
                with st.chat_message("assistant"):
                    response = generate_general_response(
                        prompt,
                        list(game_library.keys()),
                        anthropic_client,
                        menu_context
                    )
                    # Check for food order staff ping
                    if "[STAFF_PING:food_order]" in response:
                        response = response.replace("[STAFF_PING:food_order]", "").strip()
                        send_staff_ping(
                            table_id="Unknown",
                            game_title=st.session_state.current_game or "N/A",
                            question="Customer ready to order food/drinks",
                            reason="food_order"
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
                    answer, pages, sources_used = answer_question(
                        prompt,
                        st.session_state.current_game,
                        voyage_client,
                        anthropic_client,
                        menu_context
                    )
                # Check for food order staff ping
                if "[STAFF_PING:food_order]" in answer:
                    answer = answer.replace("[STAFF_PING:food_order]", "").strip()
                    send_staff_ping(
                        table_id="Unknown",
                        game_title=st.session_state.current_game or "N/A",
                        question="Customer ready to order food/drinks",
                        reason="food_order"
                    )
                # Store metadata for display
                st.session_state.last_answer_meta = {'sources_used': sources_used}

                st.markdown(answer)
                if pages:
                    st.caption(f"📄 Pages: {', '.join(map(str, pages))}")

                # Show source types if multiple document types were used
                if len(sources_used) > 1:
                    source_labels = {'rulebook': '📖 Rulebook', 'faq': '❓ FAQ', 'errata': '⚠️ Errata', 'supplement': '📑 Supplement'}
                    source_str = ' + '.join([source_labels.get(s, s.title()) for s in sorted(sources_used)])
                    st.caption(f"📚 Sources: {source_str}")

            st.session_state.messages.append({
                "role": "assistant",
                "content": answer,
                "pages": pages
            })

            # If answer offers staff assistance, rerun to show buttons immediately
            if "request staff assistance" in answer.lower():
                st.rerun()
    
    # Footer
    st.markdown("---")
    st.caption("Browse games, get rules help, check the menu, or ask for staff assistance.")

if __name__ == "__main__":
    main()
