# Deploying to Fly.io

Streamlit Cloud is fine but sleepy — first customer after a quiet stretch
watches a cold start. Fly.io keeps a small VM running 24/7 at ~$5–6/month.

## What's in this repo

| File | Purpose |
|---|---|
| `Dockerfile` | Container image (Python 3.12 slim + streamlit) |
| `.dockerignore` | Keeps `backups/`, `rulebooks/`, `golden_set/`, dev logs out of the image |
| `fly.toml` | Fly config — region, VM size, always-on, health check |

The DB (`game_library.db`, ~65 MB after the binary-embedding migration)
is already in the repo and ships inside the image.

## What you need to do (one-time)

### 1. Install the Fly CLI

```powershell
# Windows (PowerShell)
iwr https://fly.io/install.ps1 -useb | iex

# Mac / Linux
curl -L https://fly.io/install.sh | sh
```

### 2. Sign up / sign in

```bash
fly auth signup    # first time, or `fly auth login` if you already have an account
```

Fly requires a card on file. Cheapest realistic monthly is ~$5.

### 3. Launch the app

From the repo root:

```bash
fly launch --no-deploy --copy-config
```

This picks up the existing `fly.toml`. Say **no** if it asks to create a
database or Redis — you don't need either.

If the app name `merry-meeple-rules` is taken, Fly will prompt you for a
different one — pick anything and update `app = "..."` in `fly.toml` to
match.

### 4. Set your API keys as secrets

```bash
fly secrets set \
  ANTHROPIC_API_KEY="sk-ant-..." \
  VOYAGE_API_KEY="pa-..." \
  CLAUDE_MODEL="claude-sonnet-4-5" \
  VOYAGE_MODEL="voyage-3"
```

(These are stored encrypted by Fly and made available as env vars inside
the container. `config.py` already reads them.)

### 5. Deploy

```bash
fly deploy
```

First deploy takes 3–5 min (pulls the base image, installs deps, uploads
the DB). Watch the output — the last lines show your public URL.

### 6. Verify

- Open `https://<app>.fly.dev/` — should hit the game picker.
- Open `https://<app>.fly.dev/?g=wingspan` — should land in a Wingspan chat.
- Ask a real rules question, confirm citations show up.

## Redeploys after code changes

```bash
fly deploy
```

You can automate this with a GitHub Action if you want push-to-master to
redeploy — happy to wire that up when you're ready. Setup is a 5-line
`.github/workflows/fly.yml` + a Fly API token in your GitHub repo secrets.

## Custom domain (optional)

If you want `rules.merrymeeple.com` or similar:

```bash
fly certs create rules.merrymeeple.com
```

Follow the DNS instructions it prints (CNAME the subdomain to your app's
Fly hostname).

## Point the QR codes at the new URL

Once verified, regenerate your box stickers with the new base URL:
`https://<app>.fly.dev/?g=<slug>` (or the custom domain if you set one up).

Retire the Streamlit Cloud app once you're happy — no rush; both can
coexist during the switchover.

## Cost expectations

| Item | Monthly |
|---|---|
| Fly VM (shared-cpu-1x, 1 GB RAM, always-on) | ~$5–6 |
| Anthropic Claude API (~200 customers × 20 Qs) | ~$44 (Sonnet 4.5) or ~$3 (Haiku 4.5) |
| Voyage AI query embeds | ~$0 |
| Domain (if you add one) | ~$1 amortized |
| **All-in** | **~$50/mo** (or ~$9/mo on Haiku) |
