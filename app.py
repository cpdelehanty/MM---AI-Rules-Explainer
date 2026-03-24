"""
Customer-Facing Rules Assistant
Conversational chat interface with natural game selection
"""

import streamlit as st
import os
import uuid
import numpy as np
from anthropic import Anthropic
import voyageai
from dotenv import load_dotenv
from database import init_database, get_all_games, get_game_chunks
from sync_menu import should_sync, sync_menu_from_sheets, format_menu_for_prompt
from user_store import (
    normalize_phone, validate_phone, get_customer, create_customer,
    increment_visit, log_visit, add_game_to_visit, update_preferences,
    build_history_context
)

# Load environment variables
load_dotenv()

# Configuration
TOP_K_RESULTS = 5

PREFERENCE_KEYWORDS = [
    "allergic", "allergy", "vegetarian", "vegan", "gluten", "dairy",
    "kosher", "halal", "nut", "celiac", "lactose",
    "i like", "i love", "i prefer", "i enjoy", "my favorite",
    "beginner", "first time", "never played", "experienced", "hardcore",
    "birthday", "anniversary", "celebrating",
    "español", "spanish", "french", "français", "chinese", "中文",
    "russian", "русский", "arabic", "العربية", "hebrew", "עברית",
    "creole", "kreyòl", "portuguese", "português",
    "in spanish", "in french", "en español", "en français",
]

def escape_dollars(text):
    """Escape $ signs to prevent Streamlit rendering them as LaTeX"""
    return text.replace("$", "\\$")

def extract_preferences(user_message, anthropic_client, phone):
    """Extract preferences from user message and save to Google Sheets."""
    if phone == "ANON" or len(user_message) < 20:
        return
    if not any(kw in user_message.lower() for kw in PREFERENCE_KEYWORDS):
        return

    try:
        import json as _json
        extraction_prompt = f"""Given this customer message: "{user_message}"

Extract any of the following if mentioned (respond with JSON only, or {{}} if nothing):
- dietary_preferences: any food allergies, restrictions, or preferences
- game_preferences: types of games they enjoy (strategy, party, cooperative, etc.)
- experience_level: beginner, intermediate, or experienced
- language_preference: if they write in or request a non-English language (e.g. "Spanish", "French", "Haitian Creole")
- notable_info: any personal detail worth remembering (birthday, celebration, etc.)

Only extract what is explicitly stated. Do not infer."""

        response = anthropic_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            messages=[{"role": "user", "content": extraction_prompt}]
        )
        text = response.content[0].text.strip()
        # Claude sometimes wraps JSON in markdown code blocks
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        # Parse JSON from response
        if text.startswith("{"):
            data = _json.loads(text)
            if data:
                update_preferences(
                    phone,
                    dietary=data.get("dietary_preferences"),
                    game_prefs=data.get("game_preferences"),
                    experience=data.get("experience_level"),
                    notable_info=data.get("notable_info"),
                    language=data.get("language_preference"),
                )
                print(f"[PREFERENCE EXTRACTION] Saved: {data}")
            else:
                print(f"[PREFERENCE EXTRACTION] No preferences found in: {text}")
        else:
            print(f"[PREFERENCE EXTRACTION] Unexpected response format: {text}")
    except Exception as e:
        print(f"[PREFERENCE EXTRACTION] Error: {e}")

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

QUICK_ACTIONS = {
    "What games do you have?": "browse_games",
    "I need help with game rules": "rules_help",
    "What's on the menu?": "see_menu",
    "I need help from a staff member": "staff_help",
}

# Load menu (syncs from Google Sheets once per day)
@st.cache_data(ttl=86400)
def load_menu():
    """Load menu, syncing from Google Sheets if needed"""
    if should_sync():
        sync_menu_from_sheets()
    return format_menu_for_prompt()

@st.cache_resource(ttl=86400)
def pregenerate_quick_responses(_anthropic_client, game_list_str, menu_context):
    """Pre-generate responses for quick-action buttons on startup"""
    game_names = [g.strip() for g in game_list_str.split("\n") if g.strip()]
    responses = {}
    for prompt_text in QUICK_ACTIONS:
        try:
            response = generate_general_response(
                prompt_text,
                game_names,
                _anthropic_client,
                menu_context
            )
            responses[prompt_text] = response
        except Exception:
            responses[prompt_text] = None
    return responses

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

def answer_question(question, game_title, voyage_client, anthropic_client, menu_context="", customer_context=""):
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
    prompt = f"""You are a helpful board game rules assistant at The Merry Meeple cafe. Answer the customer's question based ONLY on the source documents provided below. If the customer writes in a non-English language, respond in that language.

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

When the customer asks about food, drinks, the menu, or retail items, answer from the MENU section above.
When the customer asks for the full menu or what's available, format it like this:
- List items under category headers with name and price as the bullet
- Put the full description (from the notes) in italics underneath each bullet
- Show dietary tags as plain text in parentheses after the description
Example format:
**Snacks**
- Pretzel Bites — $10
  *Pillowy soft pretzel bites with a dark, glossy crust and a generous ramekin of warm beer cheese for dunking.*
  (vegetarian)

If the customer says they want to order or is ready to order, respond helpfully and end your response with the exact tag: [STAFF_PING:food_order]
Do NOT take or confirm specific orders — just acknowledge and say staff will come by.
Do NOT invent menu items that are not listed above.
Do NOT roleplay physical actions (e.g. "slides over menu", "hands you a card"). You are a text-based assistant, not a person in the room.

{customer_context}

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

def generate_general_response(message, available_games, anthropic_client, menu_context="", customer_context=""):
    """Generate response when no game is selected"""
    game_list = "\n".join([f"• {game}" for game in sorted(available_games)])

    prompt = f"""You are a friendly assistant at The Merry Meeple, a board game cafe in Crown Heights, Brooklyn. You can help customers with:
- Browsing the game library and recommending games based on group size, experience, or preferences
- Explaining game rules and answering rules questions
- Showing the food & drink menu and recommending items
- Sharing any active deals or discounts
- Getting a staff member for orders or any other help
- Communicating in the customer's preferred language (Spanish, French, Haitian Creole, Chinese, and more)

If the customer writes in a non-English language, respond in that language.

The customer just said: "{message}"

Respond naturally and helpfully.

GAME LIBRARY:
{game_list}

When the customer asks to browse games or what games are available, format the response using markdown. Use bold headers for categories and markdown dash bullets (not bullet characters). Put each item on its own line with a blank line between categories. Follow this format EXACTLY:

**Strategy**

- **Catan**
  *Trade resources and build settlements to dominate the island.*
- **Wingspan**
  *Collect birds and build a thriving wildlife preserve in this engine-building game.*

**Family**

- **Ticket To Ride**
  *Collect train cards and claim railway routes across the country.*

MENU & FOOD/DRINK INFORMATION:
{menu_context}

When the customer asks about food, drinks, the menu, or retail items, answer from the MENU section above.
When the customer asks for the full menu or what's available, format it like this:
- List items under category headers with name and price as the bullet
- Put the full description (from the notes) in italics underneath each bullet
- Show dietary tags as plain text in parentheses after the description
Example format:
**Snacks**
- Pretzel Bites — $10
  *Pillowy soft pretzel bites with a dark, glossy crust and a generous ramekin of warm beer cheese for dunking.*
  (vegetarian)

If the customer says they want to order or is ready to order, respond helpfully and end your response with the exact tag: [STAFF_PING:food_order]
Do NOT take or confirm specific orders — just acknowledge and say staff will come by.
Do NOT invent menu items or games that are not listed above.
Do NOT roleplay physical actions (e.g. "slides over menu", "hands you a card"). You are a text-based assistant, not a person in the room.

If the customer is asking about the menu or games, give a full answer. Otherwise keep your response brief (1-3 sentences).
End with a helpful "What else can I help with?" rather than always pushing them to pick a game.

{customer_context}

Your response:"""

    response = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].text

PHONE_GATE_TEXT = """
We don't store any personal information beyond your phone number, and use this data \
only to personalize and optimize your experience: we'll recommend games tailored to you, \
remember dietary preferences, and (once a month max) we'll text you about events that you \
might be interested in based on your history.

We'll also use this data in aggregate to build a better menu and games library. \
Returning users get various discounts and perks for allowing us to use your data to \
make The Merry Meeple the best game cafe on earth.

*If you'd like to opt out, enter **999** to use a generic customer profile.*
"""

# Main app
def main():
    # Initialize session state early (needed for phone gate)
    if 'customer_phone' not in st.session_state:
        st.session_state.customer_phone = None
    if 'customer_profile' not in st.session_state:
        st.session_state.customer_profile = None
    if 'visit_id' not in st.session_state:
        st.session_state.visit_id = None
    if 'is_returning' not in st.session_state:
        st.session_state.is_returning = False

    # --- Phone gate ---
    if st.session_state.customer_phone is None:
        st.title("🎲 The Merry Meeple")
        st.markdown("Welcome! Enter your phone number to get started.")
        st.markdown(PHONE_GATE_TEXT)

        phone_input = st.text_input("Phone number", placeholder="(718) 555-1234")

        if st.button("Let's go!", use_container_width=True):
            if phone_input.strip() == "999":
                st.session_state.customer_phone = "ANON"
                st.session_state.customer_profile = {"opted_out": "TRUE", "display_name": "Guest"}
                st.session_state.visit_id = str(uuid.uuid4())
                st.session_state.is_returning = False
                st.rerun()
            elif phone_input.strip():
                normalized = normalize_phone(phone_input)
                if not validate_phone(normalized):
                    st.error("Please enter a valid 10-digit phone number.")
                else:
                    with st.spinner("Setting up your experience..."):
                        profile = get_customer(normalized)
                        if profile:
                            st.session_state.is_returning = True
                            st.session_state.customer_profile = profile
                            increment_visit(normalized)
                        else:
                            profile = create_customer(normalized)
                            st.session_state.customer_profile = profile
                            st.session_state.is_returning = False

                        st.session_state.customer_phone = normalized
                        st.session_state.visit_id = str(uuid.uuid4())
                        log_visit(normalized, st.session_state.visit_id)
                    st.rerun()
            else:
                st.error("Please enter your phone number or 999 to continue as a guest.")

        return  # Nothing else renders until phone is entered

    # --- Main app (after phone gate) ---

    # Header
    st.title("🎲 The Merry Meeple")
    st.markdown("*Your game night assistant — browse our game library, learn the rules, check out the menu, and more.*")

    # Language selector — full list with clickable buttons
    LANGUAGES = [
        ("🇺🇸", "English", "English"),
        ("🇪🇸", "Español", "Spanish"),
        ("🇫🇷", "Français", "French"),
        ("🇭🇹", "Kreyòl Ayisyen", "Haitian Creole"),
        ("🇨🇳", "中文", "Chinese"),
        ("🇷🇺", "Русский", "Russian"),
        ("🇧🇩", "বাংলা", "Bengali"),
        ("🇮🇱", "עברית", "Hebrew"),
        ("🇸🇦", "العربية", "Arabic"),
        ("🇰🇷", "한국어", "Korean"),
        ("🇯🇵", "日本語", "Japanese"),
        ("🇵🇱", "Polski", "Polish"),
        ("🇮🇹", "Italiano", "Italian"),
        ("🇵🇹", "Português", "Portuguese"),
        ("🇩🇪", "Deutsch", "German"),
    ]
    with st.expander("🌍 Available in multiple languages!"):
        for flag, native_name, eng_name in LANGUAGES:
            label = f"{flag} {native_name} ({eng_name})" if native_name != eng_name else f"{flag} {eng_name}"
            if st.button(label, key=f"lang_{eng_name}", use_container_width=True):
                # Save language preference
                if st.session_state.customer_phone and st.session_state.customer_phone != "ANON":
                    update_preferences(st.session_state.customer_phone, language=eng_name)
                # Update session profile
                if st.session_state.customer_profile:
                    st.session_state.customer_profile["language_preference"] = eng_name
                # Queue a welcome message in the selected language
                st.session_state.pending_quick_action = f"Please greet me and introduce yourself in {eng_name}."
                st.rerun()
        st.markdown("*Don't see your language? Try typing in it — we likely support it!*")

    # Initialize
    anthropic_client, voyage_client = init_clients()
    game_library = load_game_library()
    menu_context = load_menu()

    # Build customer history context for prompts
    customer_context = build_history_context(st.session_state.customer_phone)

    # Check if library is empty
    if not game_library:
        st.error("📚 No games in library yet!")
        st.info("Staff: Run `python process_rulebooks.py` to add games.")
        return

    # Pre-generate quick-action responses (cached daily)
    game_list_key = "\n".join(sorted(game_library.keys()))
    cached_responses = pregenerate_quick_responses(anthropic_client, game_list_key, menu_context)

    # Initialize remaining session state
    if 'messages' not in st.session_state:
        st.session_state.messages = []
    if 'current_game' not in st.session_state:
        st.session_state.current_game = None
    if 'pending_staff_request' not in st.session_state:
        st.session_state.pending_staff_request = None
    if 'last_question' not in st.session_state:
        st.session_state.last_question = None
    if 'pending_quick_action' not in st.session_state:
        st.session_state.pending_quick_action = None

    # Welcome back message for returning customers
    if st.session_state.is_returning and not st.session_state.messages:
        profile = st.session_state.customer_profile
        total_visits = profile.get("total_visits", "1")
        welcome_prompt = f"""You are greeting a returning customer at The Merry Meeple board game cafe.
Be warm and friendly, not creepy. Keep it to 1-2 sentences.

Customer info:
- Visit count: {total_visits}
- Last visit: {profile.get('last_seen', 'unknown')}
- Dietary preferences: {profile.get('dietary_preferences', 'none noted')}
- Game preferences: {profile.get('game_preferences', 'none noted')}
- Notable: {profile.get('notable_info', 'none')}

{customer_context}

Generate a brief, warm welcome back. Reference ONE specific thing from their history
(a game they played, how many times they've visited, etc.) but don't recite their
entire profile. Don't mention their phone number. End with asking what you can help with today."""

        try:
            welcome_response = anthropic_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=200,
                messages=[{"role": "user", "content": welcome_prompt}]
            )
            welcome_msg = welcome_response.content[0].text
            st.session_state.messages.append({"role": "assistant", "content": welcome_msg})
        except Exception:
            st.session_state.messages.append({
                "role": "assistant",
                "content": "Welcome back to The Merry Meeple! What can I help you with today?"
            })
        st.session_state.is_returning = False  # Don't re-trigger

    # Show current game if selected
    if st.session_state.current_game:
        st.info(f"🎮 Currently helping with: **{st.session_state.current_game}**")
    
    # Display chat history
    for idx, message in enumerate(st.session_state.messages):
        with st.chat_message(message["role"]):
            st.markdown(escape_dollars(message["content"]))
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
    
    # Quick-action buttons (always visible above input)
    cols = st.columns(4)
    with cols[0]:
        if st.button("🎮 Browse games", use_container_width=True):
            st.session_state.pending_quick_action = "What games do you have?"
            st.rerun()
    with cols[1]:
        if st.button("📖 Rules help", use_container_width=True):
            st.session_state.pending_quick_action = "I need help with game rules"
            st.rerun()
    with cols[2]:
        if st.button("🍽️ See the menu", use_container_width=True):
            st.session_state.pending_quick_action = "What's on the menu?"
            st.rerun()
    with cols[3]:
        if st.button("🙋 Get staff help", use_container_width=True):
            st.session_state.pending_quick_action = "I need help from a staff member"
            st.rerun()

    # Handle pending quick action from button press
    prompt = None
    is_cached_response = False
    if st.session_state.pending_quick_action:
        prompt = st.session_state.pending_quick_action
        st.session_state.pending_quick_action = None
        # Check if we have a cached response for this
        if prompt in cached_responses and cached_responses[prompt]:
            is_cached_response = True
    else:
        prompt = st.chat_input("Ask about rules, the menu, or anything else...")

    if prompt:
        # Store the question for potential staff ping
        st.session_state.last_question = prompt

        # Add user message to chat
        st.session_state.messages.append({"role": "user", "content": prompt})

        with st.chat_message("user"):
            st.markdown(prompt)

        # Extract preferences from user message (non-blocking)
        extract_preferences(prompt, anthropic_client, st.session_state.customer_phone)

        # Serve cached response for quick-action buttons (skip cache if customer has language preference)
        has_language_pref = (st.session_state.customer_profile or {}).get("language_preference", "")
        if is_cached_response and not has_language_pref:
            response = cached_responses[prompt]
            # Check for food order staff ping
            if "[STAFF_PING:food_order]" in response:
                response = response.replace("[STAFF_PING:food_order]", "").strip()
                send_staff_ping(
                    table_id="Unknown",
                    game_title=st.session_state.current_game or "N/A",
                    question="Customer ready to order food/drinks",
                    reason="food_order"
                )
            with st.chat_message("assistant"):
                st.markdown(escape_dollars(response))
            st.session_state.messages.append({"role": "assistant", "content": response})
            st.rerun()

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

                # Track game in visit history
                if st.session_state.customer_phone != "ANON" and st.session_state.visit_id:
                    add_game_to_visit(st.session_state.visit_id, detected_game)

                # Generate game intro
                with st.chat_message("assistant"):
                    with st.spinner("Loading game info..."):
                        intro_message = generate_game_intro(
                            detected_game,
                            voyage_client,
                            anthropic_client
                        )
                    st.markdown(escape_dollars(intro_message))
                
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
                            menu_context,
                            customer_context
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

                    st.markdown(escape_dollars(answer))
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
                        menu_context,
                        customer_context
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
                    st.markdown(escape_dollars(response))

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
                        menu_context,
                        customer_context
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

                st.markdown(escape_dollars(answer))
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
