"""
enrichment.py — Recruiter and hiring manager contact enrichment.

Pass 1 (free):  Regex patterns on the raw job posting HTML/text.
Pass 2 (cheap): GPT-4o-mini for jobs where regex found nothing.

Only runs on non-disqualified jobs. Enrichment is best-effort —
blank fields are left blank for manual enrichment on 4.0+ scoring jobs.
"""

import json
import logging
import os
import re
import time
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from models import Job
from prompts import RECRUITER_SYSTEM, RECRUITER_USER

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Recruiter name patterns
# ─────────────────────────────────────────────────────────────────────────────

RECRUITER_NAME_PATTERNS = [
    r"contact(?:s)?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})",
    r"reach out to\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})",
    r"recruiter[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})",
    r"talent(?:\s+acquisition)?[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})",
    r"hiring\s+manager[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})",
    r"questions[?]?\s+(?:contact|ask|email)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})",
    r"apply.*?through\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})",
    # Posted by / from LinkedIn-style
    r"posted by\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})",
]

LINKEDIN_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?linkedin\.com/in/([a-zA-Z0-9\-\_]+)/?",
    re.IGNORECASE
)

HM_HINT_PATTERNS = [
    r"you(?:'ll| will) (?:report|work) (?:to|with|directly to)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})",
    r"your\s+(?:manager|lead|team lead|engineering manager|head of)\s+(?:is|will be|:)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})",
    r"managed by\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})",
]


# ─────────────────────────────────────────────────────────────────────────────
# Main enrichment entry point
# ─────────────────────────────────────────────────────────────────────────────

def enrich_jobs(jobs: List[Job], config: dict) -> List[Job]:
    """
    Enrich recruiter/HM contact info for all non-disqualified jobs.
    Uses regex first; falls back to LLM for jobs where regex found nothing.
    """
    active_jobs = [j for j in jobs if j.tier != "disqualified"]
    logger.info(f"Enrichment: processing {len(active_jobs)} active jobs")

    llm_needed = []
    for job in active_jobs:
        # ── Step 1: Try fetching the full job page if we only have a URL ─────
        raw_text = _get_posting_text(job)

        # ── Step 2: Regex pass ────────────────────────────────────────────────
        _regex_enrich(job, raw_text)

        # ── Step 3: Queue for LLM if regex found nothing ──────────────────────
        if not job.recruiter_name and not job.hm_hint:
            job._raw_text_for_llm = raw_text  # temp attribute, cleaned after
            llm_needed.append(job)

        time.sleep(0.2)

    # ── Step 4: LLM enrichment for remaining jobs ────────────────────────────
    if config["enrichment"].get("llm_recruiter_extract", True) and llm_needed:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if api_key:
            logger.info(f"Enrichment LLM: querying {len(llm_needed)} jobs")
            for job in llm_needed:
                _llm_enrich(job, api_key, config)
                time.sleep(0.3)
        else:
            logger.warning("OPENAI_API_KEY not set — skipping LLM enrichment.")

    # Clean up temp attribute
    for job in active_jobs:
        if hasattr(job, "_raw_text_for_llm"):
            del job._raw_text_for_llm

    enriched = sum(1 for j in active_jobs if j.recruiter_name or j.hm_hint)
    logger.info(f"Enrichment complete: {enriched}/{len(active_jobs)} jobs have contact info")
    return jobs


def _get_posting_text(job: Job) -> str:
    """
    Fetch the job page if the description is thin.
    Returns description + page text (truncated).
    """
    if len(job.description) > 400:
        return job.description  # already have enough text

    # Don't fetch from ATS APIs that we already have full data from
    if job.source in ("greenhouse", "lever", "ashby"):
        return job.description

    try:
        r = requests.get(
            job.url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        # Remove scripts and styles
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        # Update description on the job object too
        if len(text) > len(job.description):
            job.description = text[:5000]
        return text[:5000]
    except Exception:
        return job.description


def _regex_enrich(job: Job, text: str) -> None:
    """Run regex patterns on posting text. Mutates job in-place."""
    if not text:
        return

    # ── Recruiter name ────────────────────────────────────────────────────────
    for pattern in RECRUITER_NAME_PATTERNS:
        m = re.search(pattern, text)
        if m:
            candidate = m.group(1).strip()
            # Sanity check — skip if it's a common false positive
            if candidate.lower() not in ("us", "our", "the", "your", "their", "this"):
                job.recruiter_name = candidate
                break

    # ── LinkedIn URL ──────────────────────────────────────────────────────────
    m = LINKEDIN_URL_PATTERN.search(text)
    if m:
        job.recruiter_linkedin = m.group(0)

    # ── HM hint ───────────────────────────────────────────────────────────────
    for pattern in HM_HINT_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            job.hm_hint = m.group(1).strip()
            break


def _llm_enrich(job: Job, api_key: str, config: dict) -> None:
    """Use GPT-4o-mini to extract recruiter/HM info from posting text."""
    raw_text = getattr(job, "_raw_text_for_llm", job.description)
    if not raw_text:
        return

    max_chars = config["filters"].get("llm_max_desc_chars", 3000)
    user_msg = RECRUITER_USER.format(
        title=job.title,
        company=job.company,
        raw_text=raw_text[:max_chars],
    )

    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": RECRUITER_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
                "max_tokens": 150,
                "temperature": 0,
            },
            timeout=20,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"].strip()
        data = json.loads(content)
        job.recruiter_name = job.recruiter_name or data.get("recruiter_name", "")
        job.recruiter_linkedin = job.recruiter_linkedin or data.get("recruiter_linkedin", "")
        job.hm_hint = job.hm_hint or data.get("hm_hint", "")
    except (json.JSONDecodeError, KeyError, requests.RequestException) as e:
        logger.debug(f"LLM enrichment failed for {job.job_id}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Salary hint extraction (regex only)
# ─────────────────────────────────────────────────────────────────────────────

SALARY_PATTERNS = [
    r"€\s*(\d[\d\.,]+)\s*(?:–|-|to)\s*€?\s*(\d[\d\.,]+)",
    r"(\d[\d\.,]+)\s*(?:–|-|to)\s*(\d[\d\.,]+)\s*(?:EUR|euro)",
    r"salary[:\s]+€?\s*(\d[\d\.,]+)",
    r"(\d{2,3})[kK]\s*(?:–|-)\s*(\d{2,3})[kK]",
]


def extract_salary_hint(text: str) -> str:
    for pattern in SALARY_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            groups = m.groups()
            if len(groups) == 2:
                return f"€{groups[0]}–€{groups[1]}"
            return f"€{groups[0]}"
    return ""


def backfill_salary_hints(jobs: List[Job]) -> List[Job]:
    """Run salary extraction on any job that doesn't have a salary hint yet."""
    for job in jobs:
        if not job.salary_hint and job.description:
            job.salary_hint = extract_salary_hint(job.description)
    return jobs
