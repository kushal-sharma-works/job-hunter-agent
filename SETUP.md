# Job Discovery Agent — Setup, Run & Test Guide

This is a **standalone repo** — everything lives at the root, not in a subdirectory.
Follow these steps in order. Each step has a verification check so you know it worked before moving on.

---

## Prerequisites

- Python 3.11+ (`python3 --version`)
- Git
- A GitHub account
- Your existing `gmail_token.json` (you already have this — see Step 5)

---

## Step 1 — Create the GitHub repo and clone it

```bash
# Create a new repo on GitHub — go to github.com/new
# Name it: job-discovery-agent
# Set to Private
# Do NOT initialise with README (you'll push files yourself)

# Clone it locally
git clone https://github.com/YOUR_USERNAME/job-discovery-agent.git
cd job-discovery-agent
```

Copy all the agent files into this directory so the root looks like this:

```
job-discovery-agent/          ← repo root
├── agent.py
├── models.py
├── state.py
├── prompts.py
├── sources.py
├── filters.py
├── enrichment.py
├── outputs.py
├── config.yml
├── portals.yml
├── requirements.txt
├── gmail_setup.py
├── job_agent_skills.md
├── SETUP.md
├── CAREER_OPS_PLAYBOOK.md
└── .github/
    └── workflows/
        └── job_discovery.yml
```

**Verify:** `ls *.py` should list agent.py, models.py, sources.py, filters.py, enrichment.py, outputs.py, state.py, prompts.py.

---

## Step 2 — Create .gitignore immediately (before anything else)

Never commit secrets or generated files to GitHub.

```bash
cat > .gitignore << 'EOF'
# Secrets
.env
gmail_token.json
gmail_credentials.json

# Generated outputs (large, noisy in diffs)
jobs_*.md
batch_*.yaml
run_log_*.json
seen_jobs.json
ind_register_cache.xlsx

# Python
.venv/
__pycache__/
*.pyc
*.pyo
.DS_Store
EOF
```

**Verify:** `cat .gitignore` — confirm `.env` and `gmail_token.json` are listed.

---

## Step 3 — Install dependencies

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install packages
pip install -r requirements.txt

# Install Playwright's Chromium (for Wellfound scraping)
playwright install chromium

# Verify key packages
python -c "import requests, yaml, bs4, openpyxl, rapidfuzz; print('all good')"
```

**Verify:** Should print `all good` with no import errors.

---

## Step 4 — Get your API keys

You need three keys. All have free tiers that cover this agent's usage.

### 4a. Adzuna — free, 250 req/day

1. Go to https://developer.adzuna.com → Sign up
2. Create an App → copy **App ID** and **App Key**

### 4b. Serper — free, 2,500 req/month

1. Go to https://serper.dev → Sign up
2. Dashboard → copy your **API Key**

### 4c. OpenAI — pay-as-you-go, ~$0.05–0.10 per full run

1. Go to https://platform.openai.com/api-keys → Create new secret key
2. Add $5 credits — enough for ~50–100 runs

### 4d. GitHub PAT — for pushing batch files to your career-ops repo

1. Go to https://github.com/settings/tokens → Generate new token (classic)
2. Tick the `repo` scope only
3. Copy the token (starts with `ghp_`)

---

## Step 5 — Create .env with all secrets

```bash
cat > .env << 'EOF'
ADZUNA_APP_ID=paste_here
ADZUNA_APP_KEY=paste_here
SERPER_API_KEY=paste_here
OPENAI_API_KEY=sk-paste_here
GITHUB_PAT=ghp_paste_here
GMAIL_ADDRESS=your.email@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
EOF
```

**Gmail App Password** (for sending the email digest — separate from OAuth token):
1. Your Google account must have 2FA enabled
2. Go to https://myaccount.google.com/apppasswords
3. App: Mail, Device: Mac → Generate → copy the 16-char password

**Verify:** `cat .env` — confirm all six values are filled in (no `paste_here` remaining).

---

## Step 6 — Drop in your Gmail token (you already have this)

You already completed the OAuth flow previously, so skip `gmail_setup.py` entirely.

```bash
# Copy your existing gmail_token.json into the repo root
cp /path/to/your/gmail_token.json ./gmail_token.json

# Verify it's valid JSON with a refresh_token field
python -c "import json; d=json.load(open('gmail_token.json')); print('token ok, scopes:', d.get('scopes','not found'))"
```

The token must have the `gmail.readonly` scope. If it was generated for a different project with a broader scope (e.g. `gmail.modify`), it still works — readonly is a subset.

**Verify:** The python check above prints `token ok` without errors.

---

## Step 7 — Apply the Gmail stack filter fix

The agent already reads your Yutori Scouts and Andrew Stutanski folders, but without this fix it doesn't filter URLs by your tech stack before passing them downstream. Open `sources.py` and find the function `_extract_jobs_from_gmail_query`. Replace the final `return jobs` line with:

```python
    # Fetch each URL and filter by tech stack before returning
    STACK_KEYWORDS = {
        "java", "kotlin", "spring", "spring boot", "backend", "full stack",
        "fullstack", "react", "typescript", "product manager", "product owner",
        "software engineer", "platform", "api", "microservice", "node"
    }
    filtered = []
    for job in jobs:
        try:
            r = requests.get(job.url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            text = r.text.lower()
            if any(kw in text for kw in STACK_KEYWORDS):
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(r.text, "html.parser")
                for tag in soup(["script", "style"]):
                    tag.decompose()
                job.description = soup.get_text(" ", strip=True)[:3000]
                filtered.append(job)
        except Exception:
            filtered.append(job)  # on fetch failure, keep it — disqualifier handles it later
    return filtered
```

**Verify:** The function `_extract_jobs_from_gmail_query` in `sources.py` no longer ends with `return jobs`.

---

## Step 8 — Edit config.yml (two required lines)

Open `config.yml` and fill in:

```yaml
# Under outputs:
career_ops_repo: "YOUR_GITHUB_USERNAME/career-ops"   # ← your actual career-ops repo
email_recipient: "your.email@gmail.com"               # ← your email address
```

Also confirm the Gmail labels match what you actually have in Gmail:

```yaml
sources:
  gmail_yutori:
    label: "Yutori Scouts"       # exact Gmail label name — case sensitive
  gmail_andrew:
    sender_contains: "stutanski" # partial match on sender — lowercase
```

**Verify:** Open Gmail, check the exact label spelling under Labels in the sidebar. Adjust if needed.

---

## Step 9 — First local run (dry run — no email, no push)

Before running for real, disable the external outputs so nothing is pushed or emailed on the first test:

```bash
# Temporarily disable email and GitHub push
sed -i '' 's/github_push: true/github_push: false/' config.yml
sed -i '' 's/email_digest: true/email_digest: false/' config.yml
```

Now run:

```bash
source .venv/bin/activate
python agent.py
```

Expected console output progression:
```
[INFO] Stage 1: Loading IND register…
[INFO] IND register: downloading from https://ind.nl/…
[INFO] IND register: 820 eligible companies in target cities
[INFO] Stage 2: Collecting jobs from all sources…
[INFO] Adzuna: 130–160 raw jobs
[INFO] Serper: 60–90 raw jobs
[INFO] Portals static: 40–80 jobs
[INFO] Gmail: 5–30 jobs
[INFO] Stage 3: Deduplicating…
[INFO] Stage 4: Filtering already-seen jobs…  (all new on first run)
[INFO] Stage 5: Filtering by location…
[INFO] Stage 6: Tagging IND tiers…
[INFO] Stage 7: Running LLM disqualifier filter…
[INFO] Stage 8: Enriching recruiter/HM contacts…
[INFO] Stage 9: Writing outputs…
[INFO] Summary: XX Tier 1 | XX Tier 2 | XX Disqualified
[INFO] Markdown digest written: jobs_YYYY-MM-DD.md
[INFO] Batch YAML written: batch_YYYY-MM-DD.yaml
[INFO] Pipeline complete in ~120s
```

**Verify the outputs exist:**
```bash
ls -lh jobs_*.md batch_*.yaml run_log_*.json
# Should see three files, each with today's date
```

**Read the digest:**
```bash
# In VS Code or any markdown viewer:
open jobs_YYYY-MM-DD.md
# Or in terminal:
cat jobs_YYYY-MM-DD.md | head -80
```

**Troubleshooting first run:**

| Symptom | Fix |
|---|---|
| `IND register: 0 companies loaded` | IND.nl changed their Excel URL. Open https://ind.nl/en/public-register-recognised-sponsors in a browser, find the download link, update `register_url_fallback` in config.yml |
| `Adzuna: 0 raw jobs` | Test the key directly: `curl "https://api.adzuna.com/v1/api/jobs/nl/search/1?app_id=YOUR_ID&app_key=YOUR_KEY&what=developer&where=amsterdam"` |
| `Gmail: token not found` | Check `gmail_token.json` is in the current directory (`ls gmail_token.json`) |
| `Gmail: token scope error` | Your token was created with different scopes. Re-run `python gmail_setup.py` with `gmail_credentials.json` present |
| `OpenAI: 401 error` | API key is wrong or has no credits. Check https://platform.openai.com/usage |
| All jobs are tier2 only | IND set is empty — delete `ind_register_cache.xlsx` and rerun |
| Playwright error on Wellfound | Run `playwright install chromium` again. Wellfound failure is not fatal — other sources continue |

---

## Step 10 — Re-enable outputs and run for real

```bash
# Re-enable
sed -i '' 's/github_push: false/github_push: true/' config.yml
sed -i '' 's/email_digest: false/email_digest: true/' config.yml

python agent.py
```

After this run:
- Check your email — you should receive the HTML digest
- Check your career-ops repo on GitHub — `batches/batch_YYYY-MM-DD.yaml` should appear

---

## Step 11 — Feed the batch into career-ops

```bash
# In a separate terminal, in your career-ops clone
cd ~/path/to/career-ops

# Pull the batch file the agent just pushed
git pull

# Open Claude Code and run the batch evaluator
claude
> /career-ops batch batches/batch_YYYY-MM-DD.yaml
```

career-ops evaluates every job in the batch against your CV across 6 blocks, scores 1–5, and auto-generates tailored PDFs for anything scoring 4.0+.

See `CAREER_OPS_PLAYBOOK.md` for a full guide on reading scores, tuning output, and using career-ops for both your Developer and PM goals.

---

## Step 12 — Set up GitHub Actions (automated runs)

### 12a. Commit and push

```bash
# Verify .gitignore is working — these should NOT appear:
git status  # should not show .env, gmail_token.json, *.md digests, *.yaml batches

git add .
git commit -m "feat: initial job discovery agent"
git push origin main
```

### 12b. Add GitHub Secrets

Go to your repo on GitHub → **Settings → Secrets and variables → Actions → New repository secret**

Add each of these:

| Secret name | Where to get it |
|---|---|
| `ADZUNA_APP_ID` | Step 4a |
| `ADZUNA_APP_KEY` | Step 4a |
| `SERPER_API_KEY` | Step 4b |
| `OPENAI_API_KEY` | Step 4c |
| `CAREER_OPS_PAT` | Step 4d |
| `GMAIL_ADDRESS` | your Gmail address |
| `GMAIL_APP_PASSWORD` | Step 5 |
| `GMAIL_TOKEN_B64` | see below |

**Base64-encode your Gmail token for CI:**
```bash
base64 -i gmail_token.json | tr -d '\n' | pbcopy
# Pastes the encoded token to your clipboard — paste it as GMAIL_TOKEN_B64
```

### 12c. Test the Actions workflow manually

1. Go to your repo → **Actions** tab
2. Click **Job Discovery Agent** in the left sidebar
3. Click **Run workflow** → **Run workflow** (green button)
4. Watch the live log — should complete in ~3–5 minutes
5. Go to the **Artifacts** section of the completed run — download and verify the digest

**Verify the workflow file is in the right place:**
```bash
cat .github/workflows/job_discovery.yml | head -5
# Should show: name: Job Discovery Agent
```

The workflow now runs automatically every **Monday, Wednesday, and Friday at 09:00 Amsterdam time**.

---

## Component test commands

Run these from the repo root with `.venv` active to test individual parts without running the full pipeline.

```bash
# Test IND register download
python -c "
import yaml; from filters import load_ind_companies
c = yaml.safe_load(open('config.yml'))
cos = load_ind_companies(c)
print(f'{len(cos)} companies. Sample:', list(cos)[:5])"

# Test Adzuna source
python -c "
import yaml; from dotenv import load_dotenv; load_dotenv()
from sources import adzuna_jobs
c = yaml.safe_load(open('config.yml'))
jobs = adzuna_jobs(['Java developer Amsterdam'], c)
print(f'{len(jobs)} jobs'); [print(j.title, '|', j.company) for j in jobs[:3]]"

# Test disqualifier LLM
python -c "
import yaml; from dotenv import load_dotenv; load_dotenv()
from models import Job; from filters import run_disqualifier_filter
c = yaml.safe_load(open('config.yml'))
jobs = [
  Job(url='https://a.com/1', title='Backend Eng', company='Adyen', description='Must be EU citizen.'),
  Job(url='https://a.com/2', title='Senior Eng', company='Booking.com', description='Strong Java skills required.'),
]
run_disqualifier_filter(jobs, c)
[print(j.title, '->', j.tier, j.disqualifier_reason) for j in jobs]"

# Test IND tier tagging
python -c "
import yaml; from dotenv import load_dotenv; load_dotenv()
from models import Job; from filters import load_ind_companies, tag_ind_tier
c = yaml.safe_load(open('config.yml'))
ind = load_ind_companies(c)
jobs = [Job(url='https://x/1', title='Eng', company='Adyen N.V.'),
        Job(url='https://x/2', title='Eng', company='SomeUnknownStartup BV')]
tag_ind_tier(jobs, ind, c)
[print(j.company, '->', j.tier) for j in jobs]"
```

---

## Maintenance

| Task | How |
|---|---|
| Add a company to scan | Add entry to `portals.yml` with Greenhouse/Lever/Ashby slug |
| Tune search keywords | Edit `search.keywords` in `config.yml` |
| Tune disqualifier logic | Edit `DISQUALIFIER_SYSTEM` in `prompts.py`, see `job_agent_skills.md` |
| Add a new job source | Add a function to `sources.py`, call it in `agent.py → collect_jobs()` |
| Reset deduplication | Delete `seen_jobs.json` — next run re-processes everything |
| Force IND re-download | Delete `ind_register_cache.xlsx` — next run re-downloads |
| Change run schedule | Edit `cron:` line in `.github/workflows/job_discovery.yml` |
| Gmail token expired | Run `python gmail_setup.py` locally, re-base64 the new token, update `GMAIL_TOKEN_B64` secret |

