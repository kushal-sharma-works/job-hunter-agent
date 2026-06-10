# career-ops Playbook
## Using career-ops for Every Goal in Your NL Job Search

This guide assumes the job discovery agent is running and pushing `batch_YYYY-MM-DD.yaml`
files to your career-ops repo. It covers how to get the most out of career-ops for
your specific situation: Indian national, NL visa sponsorship required, dual track
(Developer + PM), ~5 years Maersk experience, targeting Amsterdam.

**You will be running career-ops via Gemini CLI**, not Claude Code. career-ops supports Gemini CLI natively — all 15 slash commands are available using the same `modes/*.md` evaluation logic. Everything in this guide uses `gemini` as the entry point.

---

## Installing career-ops and setting up Gemini CLI

This is a one-time setup, separate from the job discovery agent repo.

```bash
# 1. Clone career-ops
git clone https://github.com/santifer/career-ops.git
cd career-ops

# 2. Install Node dependencies and Playwright (needed for PDF generation)
npm install
npx playwright install chromium

# 3. Run the doctor to verify prerequisites
npm run doctor
```

The `npm run doctor` command provides a pass/fail checklist for: Node.js version compatibility, presence of `node_modules` and Playwright Chromium binaries, existence of required user files (`cv.md`, `config/profile.yml`, `portals.yml`), and readiness of data directories.

```bash
# 4. Set up your config files (these are YOURS — never overwritten by updates)
cp config/profile.example.yml config/profile.yml   # edit with your details
cp templates/portals.example.yml portals.yml        # add NL companies

# 5. Create cv.md — your full CV in markdown, in the project root
# (Create this file yourself — paste your CV content into it)

# 6. Set up Gemini CLI
npm install -g @google/gemini-cli
gemini auth    # uses your Google account — free tier, no billing needed

# 7. Start a session
cd career-ops
gemini
```

The `GEMINI.md` file is auto-loaded as context. All 15 commands are defined in `.gemini/commands/*.toml`.

**Free tier note:** The free tier uses `gemini-2.5-flash` at 15 RPM, 1M tokens/day — enough for a serious job search at zero cost. You can also use the API script directly: `node gemini-eval.mjs "JD text here"` or `node gemini-eval.mjs --file ./jds/my-job.txt`.

### Your personal files — the ones that matter

Two Markdown files serve as the primary context for all AI evaluations: `cv.md` (your full professional history — the source of truth for all evaluations and PDF generation) and `article-digest.md` (optional — a collection of metrics and proof points from projects, used to provide evidence during evaluations).

The `article-digest.md` is more valuable than it sounds. Populate it with: quantified outcomes from Maersk projects ("reduced deployment time by 40%"), any product metrics you can claim ownership of, and anything from your PM coursework that has a measurable result. career-ops pulls from this file when tailoring your CV to each role.

The `modes/_profile.md` file is a user-layer file that overrides defaults with your personal archetypes, narrative, and negotiation scripts. It's copied from `modes/_profile.template.md` and is never auto-updated. This is the file where you define what "a great role for me" looks like — career-ops uses it to weight the evaluation.

### Onboarding the system — the first week matters

The first evaluations won't be great. The system has no idea who you are until you feed it context. Treat the first hour like onboarding a new recruiter: the first week they need to learn about you, then they become invaluable.

In your first session, before evaluating any jobs, ask Gemini to adapt the system to you:

```
> Change the archetypes to backend engineering + Technical PM roles
> My target market is the Netherlands, Amsterdam area
> I need a kennismigrant visa — I require IND-registered or PAYSE-route companies
> My strongest proof points are [describe your best Maersk projects]
```

The system will update `modes/_profile.md` accordingly. Every subsequent evaluation reads this file.

---

## What career-ops actually does

career-ops is agentic: it navigates career pages with Playwright, evaluates fit by reasoning about your CV vs the job description (not keyword matching), and adapts your resume per listing.

When you run an evaluation in Gemini CLI, it:

1. Fetches the job description (from URL or pasted text)
2. Evaluates fit against your `cv.md` across **7 scored blocks** (Blocks A–G, see below)
3. Produces a composite score **0–5.0**
4. For anything scoring **4.0+**, generates a **tailored, ATS-optimised PDF** with your CV adjusted for that specific role
5. Logs the result to `data/applications.md`

Below 4.0 out of 5 is a flagged skip. The system strongly recommends against applying to anything scoring below 4.0/5. Your time and the recruiter's time are both respected.

You never read a job description manually until career-ops has already told you it's worth your time.

---

## The 7 scoring blocks — what they mean for you

career-ops uses a 7-block evaluation structure (Blocks A–G) drawn from `modes/_shared.md`. Each block is scored, then combined into a 0–5.0 composite. Understanding these helps you interpret scores and know when to override.

| Block | What it checks | Why it matters for you |
|---|---|---|
| **A — CV Match** | How well your experience maps to the JD requirements | Your strongest block for dev roles. Maersk's Java/Kotlin/Spring Boot stack transfers directly. |
| **B — North Star alignment** | How well the role matches your stated career direction (in `_profile.md`) | Set this to "platform engineering + PM transition" and career-ops will score PM-adjacent roles higher |
| **C — Compensation** | Salary range vs. your floor (and kennismigrant threshold ~€5,688/month gross) | career-ops flags roles where the salary is too low to qualify for the visa — useful automatic filter |
| **D — Cultural signals** | Work style, team structure, company stage signals from the JD | Important for your NL preference: look for "flat structure", "no after-hours", "async-first" signals |
| **E — Red flags** | Explicit disqualifiers, unrealistic requirements, warning language | Overlaps with the discovery agent's disqualifier filter — a second pass with reasoning |
| **F — Role scope** | IC vs. tech lead vs. PM, growth trajectory | Critical for your PM track — a "Technical PM" or "Product Engineer" role scores higher than pure IC if your `_profile.md` targets PM |
| **G — Ghost job detection** | Posting legitimacy — signs of a fake or stale listing | Saves you from applying to jobs that were never real or were filled months ago |

---

## Developer track — getting the most out of evaluations

### How to feed the agent batch batch file from the discovery agent

The discovery agent pushes `batch_YYYY-MM-DD.yaml` to your career-ops repo. Pull it, then process in Gemini CLI. The batch system uses sub-agents to process 10+ offers in parallel via `batch-runner.sh`.

```bash
cd ~/career-ops
git pull

# Start Gemini CLI
gemini

# Evaluate your full batch (paste or reference the batch file)
> /career-ops-evaluate --file ./batches/batch_2025-06-10.yaml

# Or evaluate a single URL directly
> /career-ops "https://boards.greenhouse.io/adyen/jobs/12345"

# Or paste raw JD text
> /career-ops "We are looking for a Senior Backend Engineer with Java and Kotlin..."
```

You can also use the Node script directly without entering the CLI:
```bash
node gemini-eval.mjs --file ./batches/batch_2025-06-10.yaml
```

### Reading developer scores

| Score | What it means | Action |
|---|---|---|
| 4.5–5.0 | Strong match, your CV needs minimal adjustment | Apply within 24h. PDF is already generated. |
| 4.0–4.4 | Good match, some tailoring done | Review the PDF, check the block that pulled it below 4.5 |
| 3.5–3.9 | Partial match — you can do the job but the JD uses different language | Apply if it's Tier 1 (IND-registered) — the visa path is clean |
| 3.0–3.4 | Stretch role or significant stack mismatch | Only apply if the company is very high-value to you (ASML, Adyen, etc.) |
| Below 3.0 | career-ops flagged a hard mismatch | Skip unless you want to practice interviews |

### The 3.5 exception rule for Tier 1

Because IND-registered companies are a scarce subset, lower your threshold for them:
- Tier 1 + score 3.5+ → apply
- Tier 2 + score 4.0+ → apply
- Tier 2 + score 3.5–3.9 → only apply if recruiter contact is available

### Improving your scoring in career-ops

If the same block keeps scoring low across multiple jobs, your source files likely underrepresent that area. Common fixes for your situation:

**Block A (CV Match) scoring low:** Your `cv.md` may not list specific frameworks prominently enough. Make sure `Java 17+, Kotlin, Spring Boot 3, Spring Data JPA, REST APIs, React 18, TypeScript` appear in a dedicated Skills section at the top — not buried in bullet points.

**Block B (North Star) scoring low for non-logistics roles:** Edit `modes/_profile.md` to describe your platform/API work at Maersk in terms of engineering patterns (event-driven, microservices, high-throughput pipelines) — not the logistics context. career-ops matches patterns, not industry labels.

**Block C (Compensation) flagging roles:** career-ops checks the salary range against your stated floor in `config/profile.yml`. Make sure `minimum_salary` is set to the kennismigrant threshold (€5,688/month gross in 2025 = ~€68,000/year) so low-paying roles are automatically flagged.

---

## PM track — using career-ops for product roles

This is where career-ops needs a bit of extra guidance from you.

### Create a separate PM-track CV in career-ops

Your developer CV undersells you for PM roles. In your career-ops config, create a
second profile (check career-ops docs for how to register multiple CV profiles).
Your PM CV should:

- Lead with: "5 years building B2B logistics software at Maersk, now transitioning to product"
- Highlight: Scrum Master experience, product decisions you influenced, metrics you tracked
- Include: PM coursework and any product specs, roadmaps, or discovery work you've done
- Frame Maersk projects as product outcomes, not engineering deliverables

When you run the batch, specify the PM profile for PM-track jobs:
```
> /career-ops batch batches/batch_2025-06-10.yaml --profile pm
```

Or process in two passes — developer profile for dev roles, PM profile for PM roles.

### PM role types and what scores to target

| Role type | What career-ops looks for | Realistic score range |
|---|---|---|
| Technical PM / Platform PM | Engineering depth + product thinking | 4.0–4.5 (your sweet spot) |
| Associate / Junior PM | Education + structured thinking + some domain | 3.5–4.0 |
| Product Owner | Scrum experience + backlog ownership | 4.0–4.5 (Scrum Master background helps) |
| Senior PM / Group PM | Proven product ownership with metrics | 2.5–3.5 (too early — don't apply yet) |

The sweet spot for your NL PM transition is **Technical PM or Platform PM at a scale-up** —
companies like Adyen, Mollie, Catawiki, Sendcloud, or Backbase where your engineering
background is a genuine differentiator, not something to explain away.

---

## The visa narrative in career-ops output

When career-ops generates a cover letter or application notes, it may not handle the
visa framing the way you've decided to handle it. Add the following instruction to your
career-ops profile configuration:

```
Visa note: Never lead with visa requirements. If a cover letter requires a bio paragraph,
end it with: "As a highly skilled migrant, I require a kennismigrant visa — a standard
two-week process for IND-recognised sponsors requiring no entity setup."
Only include this line if the job is at an IND-registered company (tier1). For tier2
jobs, omit visa mention entirely from the cover letter.
```

The batch YAML the agent generates includes the `tier` field, so career-ops can
conditionally include or exclude this line based on tier.

---

## Using the tracker strategically

career-ops logs every evaluated job. Your tracker gives you data to act on.

### Pattern analysis — do this weekly

```bash
# In your career-ops directory
cat tracker.csv | sort -t',' -k4 -rn | head -20
# Top 20 jobs by score
```

Look for:
- Which **companies** are generating your highest scores → prioritise those for manual recruiter outreach
- Which **tech stacks** keep scoring you high → are they in your keywords in config.yml?
- Which **score ranges** you're mostly landing in → if you're clustering at 3.5–3.8, your CV needs work in a specific block

### The outreach decision tree

```
career-ops score ≥ 4.0 AND Tier 1 (IND)
  → Apply immediately with generated PDF
  → If recruiter name in digest → connect on LinkedIn the same day (message in PLAYBOOK Section 8)
  → Don't wait

career-ops score ≥ 4.0 AND Tier 2 (PAYSE route)
  → Apply with generated PDF
  → PAYSE framing only comes up if they show interest

career-ops score 3.5–3.9 AND Tier 1
  → Apply with generated PDF (visa path is clean, worth the shot)
  → Send LinkedIn connection request but no message yet — wait for application acknowledgment

career-ops score 3.5–3.9 AND Tier 2
  → Apply only if it's a company you specifically want (check portals.yml priority list)

career-ops score < 3.5
  → Skip unless the company is a dream company
```

---

## The LEGO recommender play

You have a former German manager now at LEGO who is a potential recommender.
career-ops can help you maximise this.

If any LEGO-adjacent company appears in your batch (LEGO itself, or companies in their
supply chain / digital / platform space), use career-ops to generate the application
materials, then:

1. **Before applying**: Message your LEGO contact on LinkedIn. Don't ask for a referral — ask for a 20-minute call to "get their perspective on the NL tech market" since they've made the Germany → NL jump. This plants the seed.
2. **At a natural moment in the call**: "I've actually been applying to a few companies in this space — including [X]. Would you feel comfortable putting my name forward or being listed as a reference?"
3. **If yes**: Update the career-ops cover letter with "referred by [Name], [Title] at LEGO" — this bypasses ATS screening entirely.

Your career-ops PDF quality makes this ask easier — you can send it to them before the call as context.

---

## Outreach message templates for 4.0+ jobs

When career-ops scores a job 4.0+ and there's a recruiter name in the digest, send a
LinkedIn connection request with this note (adjust per role):

**For developer roles:**
> Hi [Name], I came across the [Title] role at [Company] — strong match with my 5 years of Java/Kotlin/Spring Boot work at Maersk. I'd love to connect and learn more about the team.

**For PM roles:**
> Hi [Name], I noticed the [Title] role at [Company]. My background is 5 years building B2B software at Maersk + product management training — happy to share more if there's a fit.

Keep it to two sentences. No CV attached in the connection request. No mention of visa.

---

## Interview prep using career-ops output

When you get an interview, open Claude Code and run:

```
> /career-ops interview-prep [job-url or tracker-id]
```

This generates (if your career-ops version supports it — check the docs):
- Key talking points mapped to the job's required skills
- Likely technical screening questions based on the stack
- Behavioural questions based on the role level and domain
- Your strongest stories from your CV mapped to the STAR format

If your career-ops version doesn't have this command yet, you can do it manually:
paste the job description and your CV into a Claude conversation and ask:
"Generate 10 likely interview questions for this role given my background, with suggested answers."

---

## Monthly pipeline review

At the start of each month, spend 30 minutes reviewing the full tracker:

1. **Application rate**: How many 4.0+ jobs in the batch → how many applications sent?
   - If gap is large: you're not pulling the trigger fast enough on good matches
2. **Response rate**: Applications sent → recruiter/hiring manager responses?
   - If below 20%: CV needs iteration — take the lowest-scoring block and fix it
   - If below 10%: Likely a title or keyword mismatch — adjust config.yml search terms
3. **Interview conversion**: Responses → first calls?
   - If low: Your LinkedIn profile likely needs work — career-ops can't fix what happens before someone clicks your profile
4. **Tier split**: Are you mostly seeing Tier 1 or Tier 2?
   - If mostly Tier 2: Add more IND-registered companies to portals.yml

---

## Quick reference

| Command | What it does |
|---|---|
| `/career-ops batch batches/batch_YYYY-MM-DD.yaml` | Evaluate full agent batch |
| `/career-ops url https://...` | Evaluate a single job URL |
| `/career-ops batch ... --profile pm` | Evaluate with PM-track CV profile |
| `/career-ops tracker` | Open the tracker |
| `/career-ops interview-prep [id]` | Generate interview prep for a tracked job |

---

## The full weekly rhythm

```
Mon/Wed/Fri  09:00  Agent runs automatically (GitHub Actions)
             09:05  Email digest lands in your inbox
             10:00  Open career-ops, run /career-ops batch on the new file
             10:30  Review 4.0+ PDFs — apply to Tier 1 immediately, queue Tier 2
             11:00  LinkedIn outreach to recruiters for 4.0+ Tier 1 roles
             
Sunday       30min  Weekly tracker review — adjust keywords if needed
             
Monthly      30min  Pipeline review (see above)
```

The goal is that by the time you sit down to apply, every document is already generated,
every company is already visa-classified, and your only decision is yes or no.
