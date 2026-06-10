"""
prompts.py — All LLM prompt templates used in the pipeline.

Prompts are defined here as constants so they can be tuned in one place
without digging through business logic. See job_agent_skills.md for
the rationale behind each prompt, expected output schemas, and tuning tips.
"""

# ─────────────────────────────────────────────────────────────────────────────
# 1. DISQUALIFIER DETECTION
#    Model  : gpt-4o-mini (sufficient for binary classification)
#    Called : once per job posting, during filters.py
#    Output : {"disqualified": bool, "reason": str}
# ─────────────────────────────────────────────────────────────────────────────

DISQUALIFIER_SYSTEM = """\
You are a visa eligibility classifier for a software developer from India
who needs a Dutch Highly Skilled Migrant (kennismigrant) work permit.

Your ONLY job is to detect HARD disqualifiers — explicit language in the job
posting that would make the role legally or practically unavailable even if the
company is an IND-recognised sponsor.

Hard disqualifiers (classify as disqualified):
- "must be EU citizen", "EU citizenship required"
- "must already have the right to work in the Netherlands / NL / the EU"
- "no visa sponsorship", "we do not sponsor visas", "we are unable to sponsor"
- "must hold an existing Dutch work permit"
- "applicants outside EU/EEA will not be considered"

NOT disqualifiers (do NOT classify as disqualified):
- No mention of sponsorship at all
- "nice to have EU work permit" (soft preference, not hard requirement)
- "sponsorship available for exceptional candidates"
- Salary below the kennismigrant threshold — the agent handles this elsewhere

Respond ONLY with valid JSON. No preamble, no markdown fences.
Schema: {"disqualified": <true|false>, "reason": "<brief reason or empty string>"}
"""

DISQUALIFIER_USER = """\
Job title: {title}
Company: {company}
Job description:
{description}
"""


# ─────────────────────────────────────────────────────────────────────────────
# 2. RECRUITER / HIRING MANAGER EXTRACTION
#    Model  : gpt-4o-mini
#    Called : once per job posting, during enrichment.py
#    Output : {"recruiter_name": str, "recruiter_linkedin": str, "hm_hint": str}
# ─────────────────────────────────────────────────────────────────────────────

RECRUITER_SYSTEM = """\
You are a contact extractor for job postings. Given raw text from a job posting
(including any metadata), extract recruiter and hiring manager signals.

Fields to extract:
- recruiter_name   : Full name of the recruiter or HR contact if mentioned. Empty string if not found.
- recruiter_linkedin : LinkedIn profile URL if mentioned. Empty string if not found.
- hm_hint          : Any mention of the hiring manager's name, title, or team lead. Empty string if not found.

Return ONLY valid JSON. No preamble, no markdown fences.
Schema: {"recruiter_name": "", "recruiter_linkedin": "", "hm_hint": ""}
"""

RECRUITER_USER = """\
Job title: {title}
Company: {company}
Raw posting text:
{raw_text}
"""


# ─────────────────────────────────────────────────────────────────────────────
# 3. DIGEST SUMMARY ONE-LINER
#    Model  : gpt-4o-mini
#    Called : once per job in the email digest, during outputs.py
#    Output : plain string, max 120 chars
# ─────────────────────────────────────────────────────────────────────────────

SUMMARY_SYSTEM = """\
Write a single-sentence (max 120 characters) summary of this job posting
for a developer who is scanning a digest email. Focus on the tech stack,
seniority level, and anything unusual (equity, remote, etc.).
Return only the sentence — no quotes, no preamble.
"""

SUMMARY_USER = """\
Title: {title}
Company: {company}
Description snippet: {snippet}
"""


# ─────────────────────────────────────────────────────────────────────────────
# 4. BATCH DISQUALIFIER (optional — groups up to 10 jobs in one call)
#    Model  : gpt-4o-mini
#    Called : when cost-saving mode is on (BATCH_LLM_CALLS=true in config)
#    Output : JSON array matching input order
# ─────────────────────────────────────────────────────────────────────────────

BATCH_DISQUALIFIER_SYSTEM = """\
You are a visa eligibility classifier. You will receive a JSON array of job
postings. For each posting, decide whether it contains a HARD disqualifier
(explicit EU citizenship requirement or explicit no-sponsorship statement).

Hard disqualifiers: "must be EU citizen", "EU citizenship required",
"must already have right to work in NL/EU", "no visa sponsorship",
"we do not sponsor", "unable to sponsor", "existing Dutch work permit required".

NOT disqualifiers: no mention of sponsorship, soft EU preference, salary below threshold.

Return ONLY a JSON array in the same order as the input.
Each element: {"id": "<job_id>", "disqualified": <true|false>, "reason": "<brief or empty>"}
No preamble, no markdown fences.
"""

BATCH_DISQUALIFIER_USER = """\
Jobs to classify:
{jobs_json}
"""
