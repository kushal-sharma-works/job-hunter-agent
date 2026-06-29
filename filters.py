"""
filters.py — Two-stage filtering pipeline.

Stage 1 — IND register check:
    Downloads the IND recognised sponsors Excel, builds a fuzzy company-name
    lookup set, and tags each job as tier1 / tier2.

Stage 2 — GPT-4o-mini disqualifier scan:
    Sends job descriptions to GPT-4o-mini (batched) and marks anything with
    explicit EU-only or no-sponsorship language as "disqualified".
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Set, Tuple

import requests
import yaml
from bs4 import BeautifulSoup

from models import Job
from prompts import (
    DISQUALIFIER_SYSTEM,
    DISQUALIFIER_USER,
    BATCH_DISQUALIFIER_SYSTEM,
    BATCH_DISQUALIFIER_USER,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — IND register
# ─────────────────────────────────────────────────────────────────────────────

IND_COLUMNS = {
    "name": ["naam organisatie", "organisation", "name", "naam"],
    "city": ["stad", "city", "plaats"],
    "type": ["type aanvrager", "typeaanvrager", "type", "category"],
}

HIGHLY_SKILLED_KEYWORDS = [
    "kennismigrant", "highly skilled", "high skilled", "regular labour",
    "regulier arbeid", "blue card", "essentieel"
]


def load_ind_companies(config: dict) -> Set[str]:
    """
    Scrape IND recognised sponsors from the HTML table at ind.nl.
    IND removed Excel downloads — the register is now a web table only.
    Caches result as a text file (one company per line).
    """
    cache_file = Path("ind_register_cache.txt")
    cache_days = config["ind"].get("cache_days", 7)

    # ── Use cache if fresh ───────────────────────────────────────────────────
    if cache_file.exists():
        age_days = (datetime.now() - datetime.fromtimestamp(cache_file.stat().st_mtime)).days
        if age_days < cache_days:
            companies = set(cache_file.read_text(encoding="utf-8").splitlines())
            logger.info(f"IND register: using cache ({age_days}d old, {len(companies)} companies)")
            return companies

    # ── Scrape HTML table ────────────────────────────────────────────────────
    url = "https://ind.nl/en/public-register-recognised-sponsors/public-register-work"
    logger.info(f"IND register: scraping {url}")
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        companies = set()
        for row in soup.select("table tr"):
            cells = row.find_all("td")
            if cells:
                name = cells[0].get_text(strip=True)
                if name:
                    companies.add(_normalize_company(name))
        if companies:
            cache_file.write_text("\n".join(companies), encoding="utf-8")
            logger.info(f"IND register: {len(companies)} companies scraped and cached")
            return companies
        logger.warning("IND register: no companies found in HTML table")
        return set()
    except Exception as e:
        logger.error(f"IND register scrape failed: {e}")
        return set()


def _normalize_company(name: str) -> str:
    """Normalise company name for fuzzy matching."""
    # Remove legal suffixes
    name = re.sub(
        r"\b(B\.?V\.?|N\.?V\.?|BV|NV|Ltd|LLC|Inc|GmbH|SE|Holding|Group|B\.V|N\.V)\b",
        "", name, flags=re.IGNORECASE
    )
    # Remove punctuation, lowercase, collapse whitespace
    name = re.sub(r"[^a-z0-9\s]", "", name.lower())
    return re.sub(r"\s+", " ", name).strip()


def tag_ind_tier(jobs: List[Job], ind_companies: Set[str], config: dict) -> List[Job]:
    """
    Mutates each Job in-place:
      - ind_registered = True  → tier = "tier1"
      - ind_registered = False → tier = "tier2" (unless already disqualified)

    Uses rapidfuzz for fuzzy matching if available, falls back to exact match.
    """
    if not ind_companies:
        logger.warning("IND company set is empty — all jobs will be tier2 by default.")
        for job in jobs:
            if job.tier == "unknown":
                job.tier = "tier2"
        return jobs

    threshold = config["ind"].get("fuzzy_threshold", 85)
    use_fuzzy = _check_rapidfuzz()

    matched = 0
    for job in jobs:
        if not job.company:
            job.tier = "tier2"
            continue

        norm = _normalize_company(job.company)
        is_ind = False

        if use_fuzzy:
            from rapidfuzz import process, fuzz
            result = process.extractOne(norm, ind_companies, scorer=fuzz.token_sort_ratio)
            if result and result[1] >= threshold:
                is_ind = True
        else:
            # Exact match fallback
            is_ind = norm in ind_companies

        job.ind_registered = is_ind
        if job.tier == "unknown":
            job.tier = "tier1" if is_ind else "tier2"
        elif job.tier != "disqualified":
            job.tier = "tier1" if is_ind else "tier2"

        if is_ind:
            matched += 1

    logger.info(f"IND tier tagging: {matched} tier1, {len(jobs) - matched} tier2")
    return jobs


def _check_rapidfuzz() -> bool:
    try:
        import rapidfuzz
        return True
    except ImportError:
        logger.warning("rapidfuzz not installed — using exact IND matching. Run: pip install rapidfuzz for better results.")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — GPT-4o-mini disqualifier scan
# ─────────────────────────────────────────────────────────────────────────────

def run_disqualifier_filter(jobs: List[Job], config: dict) -> List[Job]:
    """
    Send job descriptions to GPT-4o-mini (batched) to detect hard disqualifiers.
    Mutates jobs in-place. Returns the same list with tier/disqualifier fields updated.
    """
    if not config["filters"].get("llm_disqualifier", True):
        logger.info("LLM disqualifier filter disabled in config — skipping.")
        return jobs

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set — skipping LLM disqualifier filter.")
        return jobs

    batch_size = config["filters"].get("llm_batch_size", 10)
    max_chars = config["filters"].get("llm_max_desc_chars", 3000)

    # Only process jobs that haven't been classified yet (skip already-disqualified)
    to_process = [j for j in jobs if j.tier != "disqualified"]

    logger.info(f"Disqualifier filter: processing {len(to_process)} jobs in batches of {batch_size}")
    processed = 0

    for i in range(0, len(to_process), batch_size):
        batch = to_process[i : i + batch_size]
        if batch_size == 1:
            _classify_single(batch[0], api_key, max_chars)
        else:
            _classify_batch(batch, api_key, max_chars)
        processed += len(batch)
        logger.debug(f"Disqualifier filter: {processed}/{len(to_process)} done")
        time.sleep(0.5)  # respect rate limits

    disqualified = sum(1 for j in jobs if j.tier == "disqualified")
    logger.info(f"Disqualifier filter: {disqualified} jobs disqualified")
    return jobs


def _classify_single(job: Job, api_key: str, max_chars: int) -> None:
    """Classify one job with a single GPT call."""
    desc = job.description[:max_chars] if job.description else "(no description available)"
    user_msg = DISQUALIFIER_USER.format(
        title=job.title,
        company=job.company,
        description=desc,
    )
    result = _call_openai(DISQUALIFIER_SYSTEM, user_msg, api_key)
    if result:
        _apply_disqualifier_result(job, result)


def _classify_batch(batch: List[Job], api_key: str, max_chars: int) -> None:
    """Classify multiple jobs in a single GPT call (cheaper)."""
    jobs_payload = [
        {
            "id": j.job_id,
            "title": j.title,
            "company": j.company,
            "description": j.description[:max_chars],
        }
        for j in batch
    ]
    user_msg = BATCH_DISQUALIFIER_USER.format(jobs_json=json.dumps(jobs_payload, ensure_ascii=False))
    raw = _call_openai(BATCH_DISQUALIFIER_SYSTEM, user_msg, api_key)
    if not raw:
        return

    try:
        results = json.loads(raw)
        result_map = {r["id"]: r for r in results}
        for job in batch:
            if job.job_id in result_map:
                _apply_disqualifier_result(job, result_map[job.job_id])
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Batch disqualifier parse error: {e}. Raw: {raw[:200]}")
        # Fallback — classify individually
        for job in batch:
            _classify_single(job, api_key, max_chars)


def _apply_disqualifier_result(job: Job, result: dict) -> None:
    if result.get("disqualified"):
        job.tier = "disqualified"
        job.disqualifier_found = True
        job.disqualifier_reason = result.get("reason", "LLM flagged")


def _call_openai(system: str, user: str, api_key: str, model: str = "gpt-4o-mini") -> str:
    """Make a single OpenAI chat completion call. Returns response text or empty string."""
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": 200,
                "temperature": 0,
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"OpenAI call failed: {e}")
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Location filter — drop jobs clearly outside NL
# ─────────────────────────────────────────────────────────────────────────────

NL_LOCATION_KEYWORDS = {
    "amsterdam", "rotterdam", "utrecht", "hague", "den haag", "eindhoven",
    "delft", "leiden", "haarlem", "almere", "netherlands", "nederland",
    "nl", "schiphol", "remote"  # include remote — user can decide
}

NON_NL_COUNTRIES = {
    "germany", "france", "spain", "italy", "poland", "uk", "england",
    "london", "berlin", "paris", "madrid", "warsaw", "dublin", "ireland",
    "sweden", "denmark", "switzerland", "austria", "belgium",
    "united states", "usa", "canada", "australia", "india", "remote only"
}


def filter_by_location(jobs: List[Job], config: dict) -> List[Job]:
    """Remove jobs whose location is clearly not in the Netherlands."""
    allowed = {loc.lower() for loc in config["search"].get("allowed_locations", [])}
    kept = []
    dropped = 0
    for job in jobs:
        if not job.location:
            kept.append(job)  # no location data — keep (IND/disqualifier filters handle it)
            continue
        loc = job.location.lower()
        if any(kw in loc for kw in NON_NL_COUNTRIES) and not any(kw in loc for kw in NL_LOCATION_KEYWORDS):
            dropped += 1
            continue
        kept.append(job)

    logger.info(f"Location filter: kept {len(kept)}, dropped {dropped}")
    return kept


# ─────────────────────────────────────────────────────────────────────────────
# Deduplication by URL + title+company similarity
# ─────────────────────────────────────────────────────────────────────────────

def deduplicate(jobs: List[Job]) -> List[Job]:
    """Remove exact URL duplicates and near-duplicate title+company pairs."""
    seen_urls = set()
    seen_pairs = set()
    unique = []

    for job in jobs:
        if not job.url:
            continue
        # Normalise URL (strip trailing slash, lowercase query params)
        norm_url = job.url.rstrip("/").lower().split("?")[0]
        if norm_url in seen_urls:
            continue
        seen_urls.add(norm_url)

        # Also deduplicate by (company, title) pair
        pair = (_normalize_company(job.company), job.title.lower().strip())
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        unique.append(job)

    logger.info(f"Deduplication: {len(jobs)} → {len(unique)} unique jobs")
    return unique