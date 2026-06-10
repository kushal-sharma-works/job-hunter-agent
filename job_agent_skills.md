# Job Agent Skills — LLM Prompt Library

This file documents every LLM prompt used in the pipeline.  
When you want to tune a prompt, edit **both** this file and `prompts.py`.  
The Python constants in `prompts.py` are what the code actually runs —  
this file is the rationale, schema, and tuning guide alongside them.

---

## Skill 1 — Disqualifier Detection

**File:** `prompts.py → DISQUALIFIER_SYSTEM / DISQUALIFIER_USER`  
**Model:** `gpt-4o-mini` (temperature 0)  
**Called by:** `filters.py → run_disqualifier_filter()`  
**When:** Once per job, batched up to 10 per API call  
**Why gpt-4o-mini:** Binary classification with a fixed rule set — no reasoning depth needed. ~10× cheaper than gpt-4o.

### What it classifies as DISQUALIFIED

| Phrase in posting | Example |
|---|---|
| EU citizenship required | "must be an EU citizen" |
| Right to work required | "must already have right to work in NL" |
| No sponsorship statement | "we do not sponsor visas", "unable to sponsor" |
| Existing permit required | "must hold a valid Dutch work permit" |
| EEA-only explicit | "applicants outside EU/EEA will not be considered" |

### What it must NOT disqualify

- No mention of sponsorship at all (the majority of postings — keep these)
- Soft EU preference: "EU work permit is a plus" — still tier2
- Salary below kennismigrant threshold — handled separately
- "visa assistance available" — the opposite of a disqualifier

### Output schema

```json
{ "disqualified": true, "reason": "Explicit: must already have right to work in NL" }
{ "disqualified": false, "reason": "" }
```

### Tuning tips

- If too many false positives (good jobs getting dropped): add more examples to the "NOT disqualifiers" list in the system prompt.  
- If too many false negatives (disqualified jobs slipping through): add the exact phrasing you're seeing to the "Hard disqualifiers" list.  
- You can switch from batch (10 per call) to single (1 per call) by setting `llm_batch_size: 1` in config.yml.

---

## Skill 2 — Recruiter / HM Extraction

**File:** `prompts.py → RECRUITER_SYSTEM / RECRUITER_USER`  
**Model:** `gpt-4o-mini` (temperature 0)  
**Called by:** `enrichment.py → _llm_enrich()`  
**When:** Only for non-disqualified jobs where regex found nothing (~60–70% of jobs)  
**Note:** Regex pass in `enrichment.py` runs first — LLM is only a fallback.

### Output schema

```json
{
  "recruiter_name": "Sophie van der Berg",
  "recruiter_linkedin": "https://linkedin.com/in/sophievdberg",
  "hm_hint": "Reports to Head of Engineering, Platform team"
}
```

Return empty strings — never null — for fields not found.

### What counts as a recruiter hint

- Name + recruiter/HR/talent/TA title
- "Contact [Name] for questions"
- "Posted by [Name]" in job board metadata
- Email address format that includes a name (e.g. `s.vandenberg@company.com`)

### What counts as a HM hint

- "You will report to [Name/Title]"
- "Your manager is the [Title]"
- "Managed by the [Team] lead"

### Tuning tips

- This is best-effort. ~25–30% hit rate for recruiter name, ~15–20% for HM. That's expected.
- For 4.0+ scoring jobs, manually enrich via LinkedIn Sales Navigator or Recruiter Lite.
- Don't add LinkedIn URL if you're not certain — a wrong URL is worse than no URL.

---

## Skill 3 — Digest One-Liner Summary

**File:** `prompts.py → SUMMARY_SYSTEM / SUMMARY_USER`  
**Model:** `gpt-4o-mini` (temperature 0.3)  
**Called by:** `outputs.py` (optional — currently built into HTML email directly)  
**When:** Optionally, once per non-disqualified job for the email digest  
**Max output:** 120 characters

### Purpose

A scannable one-liner for the email digest so you can quickly decide which jobs to look at before clicking through to career-ops.

### Output example

```
Senior Java/Kotlin backend role at a Dutch fintech, €80–100K, strong equity, team of 6
```

### Tuning tips

- If summaries are too generic: add `focus on what makes this role unusual` to the system prompt.
- If summaries are too long: reduce max_tokens or add `strictly under 100 characters` to the prompt.

---

## Skill 4 — Batch Disqualifier (cost-saving mode)

**File:** `prompts.py → BATCH_DISQUALIFIER_SYSTEM / BATCH_DISQUALIFIER_USER`  
**Model:** `gpt-4o-mini`  
**Called by:** `filters.py → _classify_batch()` when `llm_batch_size > 1`  
**When:** Default — batches up to 10 jobs per API call

### Why batching matters

Single-call mode: 200 jobs × 1 call = 200 API calls  
Batch mode (10 per call): 200 jobs ÷ 10 = 20 API calls — **10× fewer calls, 10× cheaper**

At gpt-4o-mini pricing (~$0.15/1M input tokens), a full 200-job run costs roughly **$0.05–0.10** in batch mode.

### Output schema

Array in same order as input:

```json
[
  { "id": "a3f1b2c4d5e6", "disqualified": false, "reason": "" },
  { "id": "9x8y7z6w5v4u", "disqualified": true,  "reason": "must be EU citizen" }
]
```

### Fallback behaviour

If the batch call returns malformed JSON or mismatched IDs, `filters.py` automatically falls back to single-call mode for each job in the batch.

---

## IND Company Matching — not an LLM skill

This is handled by `filters.py → tag_ind_tier()` using `rapidfuzz` (fuzzy string matching).

### Why fuzzy matching

The IND register lists companies as **"Booking.com B.V."** while job postings say **"Booking.com"**.  
Exact matching would miss ~30–40% of IND-registered companies.  
`rapidfuzz.token_sort_ratio` handles: legal suffixes (B.V., N.V., Ltd), extra words, minor spelling variants.

### Threshold

Default: `fuzzy_threshold: 85` in config.yml (0–100 scale).

| Threshold | Behaviour |
|---|---|
| 95–100 | Near-exact only. Fewer false positives, more missed matches |
| 85 *(default)* | Good balance — catches B.V./N.V. stripping and minor variants |
| 70–80 | More matches, but risk of false positives (wrong company tagged as tier1) |

### To add a manual override

If a specific company is being missed or wrongly matched, add it to `portals.yml` with its correct ATS slug — those always take precedence.

---

## Prompt versioning

When you change a prompt, increment the version comment at the top of `prompts.py`:

```python
# Prompt version: v1.2  ← bump this
# Last changed: 2025-06-10
# Change: tightened disqualifier rule for "right to work" variants
```

This makes it easy to know which prompt version produced a given run log.
