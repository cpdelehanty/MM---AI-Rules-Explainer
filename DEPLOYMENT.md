# Deploying to Streamlit Cloud (Free & Browser-Accessible)

## Overview

Streamlit Cloud hosts your app for free and gives you a public URL like:
`https://merry-meeple-rules.streamlit.app`

Customers can access it from tablets or phones - no local server needed.

---

## Step 1: Prepare Your Code

### Files Needed:
```
your-repo/
├── app.py                  # Customer app (main file)
├── database.py             # Database functions
├── process_rulebooks.py    # Processing script
├── requirements.txt        # Dependencies
├── .gitignore             # Protect secrets
├── rulebooks/             # Your PDF files
│   ├── streets.pdf
│   ├── catan.pdf
│   ├── ticket_to_ride.pdf
│   ├── wingspan.pdf
│   └── azul.pdf
└── game_library.db        # SQLite database (created after processing)
```

### Important: Process PDFs Locally First

**Before deploying, run locally:**
```bash
python process_rulebooks.py
```

This creates `game_library.db` with all your processed games. **Include this file in your repo** - Streamlit Cloud will use it.

---

## Step 2: Create GitHub Repository

### Option A: GitHub Desktop (Easiest)

1. Download GitHub Desktop: https://desktop.github.com/
2. Open GitHub Desktop → File → New Repository
3. Name: `merry-meeple-rules`
4. Local path: Your project folder
5. Click "Create Repository"
6. Click "Publish repository" → Uncheck "Keep this code private" → Publish

### Option B: Command Line

```bash
cd /path/to/your/project
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/merry-meeple-rules.git
git push -u origin main
```

### Critical: Make Sure .gitignore Works

Your `.gitignore` should contain:
```
.env
__pycache__/
*.pyc
.streamlit/
```

**But NOT:**
- `game_library.db` (you NEED this in the repo)
- `rulebooks/*.pdf` (you NEED these in the repo)

These files must be committed so Streamlit Cloud can use them.

---

## Step 3: Deploy to Streamlit Cloud

### 1. Sign Up
- Go to: https://share.streamlit.io/
- Click "Sign in with GitHub"
- Authorize Streamlit

### 2. Create New App
- Click "New app"
- Select your repository: `merry-meeple-rules`
- Main file path: `app.py`
- Click "Deploy"

### 3. Add API Keys (Secrets)

**In Streamlit Cloud dashboard:**
1. Click your app → Settings (⚙️) → Secrets
2. Add your API keys in TOML format:

```toml
ANTHROPIC_API_KEY = "sk-ant-api03-YOUR-KEY-HERE"
VOYAGE_API_KEY = "pa-YOUR-KEY-HERE"
```

3. Click "Save"

### 4. Wait for Deployment

Takes 2-3 minutes. You'll see:
- 🔄 Building
- ✅ Running
- 🌐 Your public URL

---

## Step 4: Access Your App

### Your URL will be:
```
https://YOUR-USERNAME-merry-meeple-rules-HASH.streamlit.app
```

**Share this URL with customers!**

Works on:
- ✅ Cafe tablets
- ✅ Customer phones
- ✅ Any browser

---

## Updating the App

### When You Add New Games:

**Locally:**
1. Add PDF to `rulebooks/` folder
2. Run: `python process_rulebooks.py`
3. Commit changes:
   ```bash
   git add game_library.db rulebooks/
   git commit -m "Added new game"
   git push
   ```

**Streamlit Cloud automatically redeploys** when you push to GitHub.

### When You Change Code:

```bash
git add app.py  # or whatever file you changed
git commit -m "Updated UI"
git push
```

App redeploys automatically.

---

## Troubleshooting

### "App failed to load"
→ Check Streamlit Cloud logs (click "Manage app" → "Logs")
→ Common issue: Missing API keys in Secrets

### "No games in library"
→ Make sure `game_library.db` is in your repo
→ Make sure you ran `process_rulebooks.py` locally first
→ Check that `.gitignore` doesn't exclude `.db` files

### "API key not found"
→ Go to Settings → Secrets
→ Make sure keys are in TOML format (see Step 3.3)
→ Restart app after adding secrets

### Database is read-only on Streamlit Cloud
→ This is expected! Process PDFs locally, commit the database
→ Streamlit Cloud uses the database in read-only mode
→ To add games: process locally → commit → push

### "File not found: rulebooks/..."
→ Make sure PDFs are committed to GitHub
→ Check `.gitignore` doesn't exclude `.pdf` files

---

## Cost

**Streamlit Cloud Free Tier:**
- ✅ Unlimited public apps
- ✅ 1GB resources per app
- ✅ Community support
- ❌ Can't make apps private (anyone with URL can access)

For this app:
- Hosting: **$0/month**
- API calls: **~$10-50/month** (depending on usage)

---

## Custom Domain (Optional)

Want `rules.merrymeeple.com` instead of the Streamlit URL?

**Upgrade to Streamlit Cloud Pro** ($20/month):
1. Settings → Custom subdomain
2. Enter: `merry-meeple-rules`
3. Get: `https://merry-meeple-rules.streamlit.app`

Or use Cloudflare (free) to point your domain → Streamlit URL.

---

## Security Note

**Your app URL is public.** Anyone with the link can access it.

For MVP: This is fine (it's just game rules)

For production: Consider Streamlit Cloud Pro for:
- Password protection
- Custom domain
- Private repos

---

## Next Steps

1. Process your 5 PDFs locally
2. Push to GitHub
3. Deploy to Streamlit Cloud
4. Share URL with staff
5. Test on tablet/phone
6. Add QR code in cafe

**Want help with any step? Let me know!**
