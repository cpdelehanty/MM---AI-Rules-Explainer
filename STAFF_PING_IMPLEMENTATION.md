# Staff Ping System - Implementation Guide

## Current Status: UI Built, Backend Stub

The customer-facing UI is ready:
- ✅ AI offers staff help when it can't answer
- ✅ Customer sees confirmation buttons
- ✅ Customer clicks "Yes, get help" or "Check manual"
- ✅ Confirmation message shows
- ⏳ Backend notification (stub only - needs implementation)

---

## How It Works Now

**Customer experience:**
1. Asks question AI can't answer
2. AI responds: "I don't see that information in the rulebook. Would you like me to request staff assistance?"
3. Two buttons appear:
   - 📞 "Yes, get help" 
   - 📖 "Check manual"
4. If "Yes": Shows "✅ Staff notified! Someone will be with you shortly."
5. Behind scenes: `send_staff_ping()` function is called (currently just logs)

**Staff experience:**
- Currently: Nothing (stub only prints to console)
- Future: Gets notification via chosen method

---

## Implementation Options

### Option 1: Email Notification (Easiest - 1 hour)

**Best for:** Small cafe, not time-critical

**Setup:**
```python
# Install: pip install sendgrid
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

def send_staff_ping(table_id, game_title, question, reason="rules_question"):
    message = Mail(
        from_email='assistant@merrymeeple.com',
        to_emails='staff@merrymeeple.com',
        subject=f'🆘 Table {table_id} Needs Help',
        html_content=f"""
        <h3>Customer Assistance Requested</h3>
        <p><strong>Table:</strong> {table_id}</p>
        <p><strong>Game:</strong> {game_title}</p>
        <p><strong>Question:</strong> {question}</p>
        <p><strong>Type:</strong> {reason}</p>
        <p><em>Requested at {datetime.now().strftime('%I:%M %p')}</em></p>
        """
    )
    
    try:
        sg = SendGridAPIClient(os.environ.get('SENDGRID_API_KEY'))
        sg.send(message)
        return {"success": True, "message": "Staff notified by email!"}
    except Exception as e:
        return {"success": False, "message": "Notification failed. Please wave down staff."}
```

**Cost:** Free tier: 100 emails/day (plenty)  
**Pros:** Simple, reliable, no app needed  
**Cons:** Not instant, staff must check email

---

### Option 2: SMS Notification (Fast - 2 hours)

**Best for:** Time-sensitive requests, staff on floor

**Setup:**
```python
# Install: pip install twilio
from twilio.rest import Client

def send_staff_ping(table_id, game_title, question, reason="rules_question"):
    client = Client(
        os.environ.get('TWILIO_ACCOUNT_SID'),
        os.environ.get('TWILIO_AUTH_TOKEN')
    )
    
    # Get on-duty staff phone from config/database
    staff_phone = os.environ.get('ON_DUTY_STAFF_PHONE')
    
    message = client.messages.create(
        body=f"Table {table_id} needs help with {game_title}: {question[:100]}",
        from_=os.environ.get('TWILIO_PHONE_NUMBER'),
        to=staff_phone
    )
    
    return {"success": True, "message": "Staff notified by text!"}
```

**Cost:** $0.0079 per SMS (~$1/month for 100 requests)  
**Pros:** Instant, works anywhere  
**Cons:** Requires phone management, costs money

---

### Option 3: Slack Notification (Medium - 3 hours)

**Best for:** Team already uses Slack

**Setup:**
```python
# Install: pip install slack-sdk
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

def send_staff_ping(table_id, game_title, question, reason="rules_question"):
    client = WebClient(token=os.environ.get('SLACK_BOT_TOKEN'))
    
    try:
        response = client.chat_postMessage(
            channel='#cafe-assistance',
            text=f":sos: Table {table_id} needs help!",
            blocks=[
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"🆘 Table {table_id} Needs Help"}
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Game:*\n{game_title}"},
                        {"type": "mrkdwn", "text": f"*Type:*\n{reason}"}
                    ]
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*Question:*\n{question}"}
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "I'm on it!"},
                            "value": f"claim_{table_id}",
                            "action_id": "claim_request"
                        }
                    ]
                }
            ]
        )
        return {"success": True, "message": "Staff notified on Slack!"}
    except SlackApiError as e:
        return {"success": False, "message": "Notification failed."}
```

**Cost:** Free  
**Pros:** Rich formatting, staff can claim requests  
**Cons:** Requires Slack workspace, app setup

---

### Option 4: Staff Dashboard (Complex - 1-2 days)

**Best for:** Multiple staff, tracking/analytics

**Architecture:**
```
Customer App → API → Database → Staff Dashboard
                                   ↓
                              (WebSocket updates)
```

**Database Schema:**
```sql
CREATE TABLE staff_requests (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT NOW(),
    table_id VARCHAR(50),
    game_title VARCHAR(100),
    question TEXT,
    reason VARCHAR(50),
    status VARCHAR(20) DEFAULT 'pending',
    claimed_by VARCHAR(100),
    resolved_at TIMESTAMP
);
```

**Backend API (FastAPI):**
```python
from fastapi import FastAPI
from sqlalchemy.orm import Session

app = FastAPI()

@app.post("/api/staff-ping")
async def create_staff_request(request: StaffRequest, db: Session):
    db_request = StaffRequestModel(**request.dict())
    db.add(db_request)
    db.commit()
    
    # Broadcast to connected staff dashboards via WebSocket
    await broadcast_to_staff(db_request)
    
    return {"success": True, "message": "Request created"}

@app.get("/api/staff-requests")
async def get_pending_requests(db: Session):
    return db.query(StaffRequestModel).filter_by(status='pending').all()
```

**Staff Dashboard (React/Streamlit):**
- Real-time list of pending requests
- Claim button for each request
- Mark as resolved
- Historical analytics

**Cost:** Hosting ~$10-20/month  
**Pros:** Full tracking, analytics, multiple staff  
**Cons:** Significant dev time, needs hosting

---

## Recommended Implementation Path

### Phase 1: Quick Win (This Week)
**Use Email** - 1 hour to implement
- Simple, reliable
- No new infrastructure
- Good enough for MVP

### Phase 2: Upgrade (Month 2)
**Add SMS** for urgent requests
- Keep email for non-urgent
- Route based on request type:
  - Rules questions → Email
  - New game needed → SMS
  - Food/drink order → SMS

### Phase 3: Scale (Month 6)
**Build Dashboard** when you have:
- Multiple staff members
- Need for analytics
- Want to track response times
- Integration with POS/table management

---

## Wiring It Up

### Step 1: Choose Your Method
Pick email, SMS, Slack, or dashboard

### Step 2: Get API Keys
**Email (SendGrid):**
- Sign up: https://sendgrid.com/
- Free tier: 100 emails/day
- Get API key from dashboard
- Add to Streamlit Secrets: `SENDGRID_API_KEY = "..."`

**SMS (Twilio):**
- Sign up: https://twilio.com/
- Get: Account SID, Auth Token, Phone Number
- Add to Secrets:
  ```toml
  TWILIO_ACCOUNT_SID = "..."
  TWILIO_AUTH_TOKEN = "..."
  TWILIO_PHONE_NUMBER = "+1234567890"
  ON_DUTY_STAFF_PHONE = "+0987654321"
  ```

**Slack:**
- Create Slack App: https://api.slack.com/apps
- Add Bot Token Scopes: `chat:write`, `channels:read`
- Install to workspace
- Add to Secrets: `SLACK_BOT_TOKEN = "xoxb-..."`

### Step 3: Install Packages

Add to `requirements.txt`:
```
# For Email
sendgrid==6.11.0

# OR for SMS
twilio==9.0.0

# OR for Slack
slack-sdk==3.27.0
```

### Step 4: Replace Stub Function

In `app.py`, find `send_staff_ping()` and replace with your chosen implementation (see examples above).

### Step 5: Update Secrets

**Streamlit Cloud:**
- Settings → Secrets
- Add your API keys

**Local Testing:**
- Add keys to `.env` file

### Step 6: Test

```python
# Test notification manually
send_staff_ping(
    table_id="Table 5",
    game_title="Wingspan",
    question="Test notification",
    reason="rules_question"
)
```

Check that you receive the notification!

### Step 7: Deploy

```bash
git add app.py requirements.txt
git commit -m "Wire up staff notifications"
git push
```

Streamlit Cloud auto-deploys.

---

## Table ID Management

**Current limitation:** App doesn't know which table customer is at.

**Solutions:**

### Short-term: Manual Table Entry
Add at app start:
```python
if 'table_id' not in st.session_state:
    st.session_state.table_id = st.text_input("Enter your table number:")
```

### Medium-term: QR Code Per Table
- Generate unique QR code for each table
- Encode table ID in URL: `https://app.com/?table=5`
- Extract on load:
```python
table_id = st.query_params.get("table", "Unknown")
```

### Long-term: Session Management
- Customer logs in at table
- Staff assigns them to table in system
- Session persists across devices

---

## Future Enhancements

### Request Types Beyond Rules

**New Game Request:**
```python
# In UI, add "Request New Game" button
if st.button("🎲 Request a Different Game"):
    send_staff_ping(
        table_id=st.session_state.table_id,
        game_title="New game requested",
        question=f"Customer wants to switch from {current_game}",
        reason="new_game"
    )
```

**Food/Drink Order:**
```python
# After game intro
st.markdown("### Want some refreshments?")
if st.button("🍕 Order Food/Drinks"):
    send_staff_ping(
        table_id=st.session_state.table_id,
        game_title=current_game,
        question="Food/drink order request",
        reason="food_order"
    )
```

### Analytics Dashboard

Track:
- Average response time
- Most common questions (where rulebooks unclear)
- Peak request times
- Staff performance

---

## Testing Checklist

- [ ] Customer can see "request staff" offer
- [ ] Buttons appear correctly
- [ ] Clicking "Yes" sends notification
- [ ] Staff receives notification (email/SMS/Slack)
- [ ] Confirmation message shows to customer
- [ ] Can't double-request same question
- [ ] "Check manual" option works
- [ ] Works on mobile
- [ ] Table ID included in notification (if implemented)
- [ ] Different request types work (rules, game, food)

---

## Cost Summary

| Method | Setup Time | Monthly Cost | Best For |
|--------|------------|--------------|----------|
| **Email** | 1 hour | $0 | MVP, non-urgent |
| **SMS** | 2 hours | ~$1-5 | Urgent, staff on floor |
| **Slack** | 3 hours | $0 | Teams already using Slack |
| **Dashboard** | 2 days | $10-20 | Multiple locations, analytics |

---

## Next Steps

1. **Test current UI** - verify buttons work
2. **Choose notification method** (recommend email for start)
3. **Get API keys**
4. **Replace stub function** with real implementation
5. **Test end-to-end**
6. **Deploy**

**Estimated time to go live:** 2-3 hours for email implementation.

---

Questions? See the code comments in `send_staff_ping()` function for more implementation details.
