# API Key Setup Guide

## Why Use .env Files?

**Benefits:**
- ✅ Keys persist across terminal sessions
- ✅ Keys don't appear in command history
- ✅ Easy to manage multiple projects
- ✅ Prevents accidental sharing (gitignored)
- ✅ Industry standard practice

**Never:**
- ❌ Hardcode keys in Python files
- ❌ Commit .env to GitHub
- ❌ Share keys in screenshots/logs

---

## Setup Steps

### 1. Create Your .env File

**Option A: Copy the template**
```bash
cp .env.template .env
```

**Option B: Create from scratch**

Create a file named `.env` in the project root:

**Mac/Linux:**
```bash
nano .env
```

**Windows:**
```cmd
notepad .env
```

Add your keys:
```
ANTHROPIC_API_KEY=sk-ant-api03-YOUR-ACTUAL-KEY-HERE
VOYAGE_API_KEY=pa-YOUR-ACTUAL-KEY-HERE
```

Save and close.

---

### 2. Verify .env is Ignored by Git

Check that `.gitignore` contains:
```
.env
```

This prevents accidentally committing your keys to version control.

---

### 3. Install python-dotenv

```bash
pip install python-dotenv
```

(Already included in requirements.txt)

---

### 4. Run the App

```bash
streamlit run rulebook_assistant.py
```

The app automatically loads keys from `.env` - no need to set environment variables manually!

---

## How It Works

When you run the app:

```python
from dotenv import load_dotenv
load_dotenv()  # Reads .env file
```

This automatically sets environment variables from `.env`, which the app then reads:

```python
anthropic_client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
```

---

## Troubleshooting

### "API key not found"

**Check 1:** Does `.env` file exist?
```bash
ls -la .env  # Mac/Linux
dir .env     # Windows
```

**Check 2:** Are keys formatted correctly?
```bash
cat .env  # Mac/Linux
type .env # Windows
```

Should look like:
```
ANTHROPIC_API_KEY=sk-ant-api03-...
VOYAGE_API_KEY=pa-...
```

**No quotes, no spaces around `=`**

**Check 3:** Is python-dotenv installed?
```bash
pip list | grep dotenv
```

---

### "Invalid API key"

- Double-check you copied the full key
- Keys are long (50+ characters)
- Make sure no extra spaces/newlines

---

### Still using terminal export?

That's fine! Both methods work:

**Terminal (temporary - expires when terminal closes):**
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export VOYAGE_API_KEY="pa-..."
```

**.env file (permanent - loaded every time):**
```
ANTHROPIC_API_KEY=sk-ant-...
VOYAGE_API_KEY=pa-...
```

**Recommended:** Use .env for convenience.

---

## Security Best Practices

### ✅ DO:
- Keep .env in project root
- Add .env to .gitignore
- Use different keys for dev/prod
- Rotate keys periodically
- Use .env.template for sharing structure

### ❌ DON'T:
- Commit .env to GitHub
- Share keys in Slack/email
- Hardcode keys in code
- Screenshot terminal with keys visible
- Use same keys across all projects

---

## Sharing Your Project

When sharing code:

**Include:**
- ✅ .env.template (without real keys)
- ✅ .gitignore (with .env listed)
- ✅ README with setup instructions

**Don't include:**
- ❌ .env (your actual keys)
- ❌ Any files with real API keys

---

## Alternative: Streamlit Secrets (Production)

For deployed apps on Streamlit Cloud:

1. Go to app settings
2. Click "Secrets"
3. Add:
```toml
ANTHROPIC_API_KEY = "sk-ant-..."
VOYAGE_API_KEY = "pa-..."
```

The app reads from either .env (local) or Streamlit secrets (cloud) automatically.

---

## Quick Reference

| Method | Persistence | Use Case |
|--------|-------------|----------|
| **Terminal export** | Session only | Quick testing |
| **.env file** | Permanent | Local development |
| **Streamlit secrets** | Cloud-based | Production deployment |

**Recommendation:** Use .env file for local work.

---

## Example .env File

```bash
# Anthropic API Key (Claude)
ANTHROPIC_API_KEY=sk-ant-api03-AbCdEf1234567890...

# Voyage AI API Key (Embeddings)
VOYAGE_API_KEY=pa-XyZ9876543210...

# Optional: Set to "true" to enable debug logging
DEBUG=false
```

Simple as that!
