"""
agent.py — Main orchestrator for the job discovery pipeline.

Run locally:  python agent.py
Run in CI:    called by GitHub Actions via job_discovery.yml

Pipeline stages:
  1.  Load IND register
  2.  Collect raw jobs from all enabled sources
  3.  Deduplicate
  4.  Filter already-seen jobs
  5.  Location filter
  6.  IND tier tagging (tier1 / tier2)
  7.  GPT disqualifier filter
  8.  Enrichment (recruiter contact, salary backfill)
  9.  Write batch TSV + push to GitHub
  10. Mark all processed jobs as seen
"""

import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List

import yaml
from dotenv import load_dotenv

from models import Job
from state import filter_new, mark_seen
from sources import (
    adzuna_jobs,
    jobspy_jobs,
    wellfound_jobs,
    undutchables_jobs,
    gmail_jobs,
    ind_ats_jobs,
    linkedin_jobs_source,
    linkedin_posts_source,
    niche_boards_source,
)
from filters import (
    load_ind_companies,
    tag_ind_tier,
    run_disqualifier_filter,
    filter_by_location,
    deduplicate,
)
from enrichment import enrich_jobs, backfill_salary_hints
from outputs import (
    write_batch_tsv,
    push_batch_to_github,
    save_run_log,
)

# ─────────────────────────────────────────────────────────────────────────────
# Logging setup
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("agent")


# ─────────────────────────────────────────────────────────────────────────────
# Config & env
# ─────────────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open("config.yml") as f:
        return yaml.safe_load(f)


def check_env(config: dict) -> None:
    """Warn about missing environment variables before starting."""
    required = []

    if config["sources"]["adzuna"]["enabled"]:
        required += ["ADZUNA_APP_ID", "ADZUNA_APP_KEY"]

    if config["sources"].get("jobspy", {}).get("enabled", True):
        pass  # python-jobspy needs no API key

    if (
        config["sources"].get("linkedin_jobs", {}).get("enabled", True)
        or config["sources"].get("linkedin_posts", {}).get("enabled", True)
        or config["sources"].get("niche_boards", {}).get("enabled", True)
    ):
        required += ["SERPER_API_KEY"]

    if config["filters"].get("llm_disqualifier") or config["enrichment"].get("llm_recruiter_extract"):
        required += ["OPENAI_API_KEY"]

    if config["outputs"].get("github_push"):
        required += ["GH_PAT"]

    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        logger.warning(f"Missing env vars (those features will be skipped): {', '.join(missing)}")


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — Collect jobs from all sources
# ─────────────────────────────────────────────────────────────────────────────

def collect_jobs(config: dict, ind_companies: set) -> List[Job]:
    all_jobs: List[Job] = []
    src = config["sources"]
    dev_kw = config["search"]["keywords"].get("developer", [])
    pm_kw = config["search"]["keywords"].get("pm", [])
    all_kw = dev_kw + pm_kw

    # ── API / scraper sources ─────────────────────────────────────────────────
    if src["adzuna"]["enabled"]:
        _collect(all_jobs, adzuna_jobs, all_kw, config)

    if src.get("jobspy", {}).get("enabled", True):
        _collect(all_jobs, jobspy_jobs, all_kw, config)

    # ── IND-driven ATS discovery ──────────────────────────────────────────────
    if any(src.get(k, {}).get("enabled", True) for k in ("greenhouse_ind", "lever_ind", "ashby_ind")):
        _collect(all_jobs, ind_ats_jobs, list(ind_companies)[:500], config)

    # ── Scraped job boards ────────────────────────────────────────────────────
    if src["wellfound"]["enabled"]:
        _collect(all_jobs, wellfound_jobs, config)

    if src["undutchables"]["enabled"]:
        _collect(all_jobs, undutchables_jobs, config)

    # ── Gmail ─────────────────────────────────────────────────────────────────
    if src["gmail_yutori"]["enabled"] or src["gmail_andrew"]["enabled"]:
        _collect(all_jobs, gmail_jobs, config)

    # ── Brave-powered sources ─────────────────────────────────────────────────
    if src.get("linkedin_jobs", {}).get("enabled", True):
        _collect(all_jobs, linkedin_jobs_source, all_kw, config)

    if src.get("linkedin_posts", {}).get("enabled", True):
        _collect(all_jobs, linkedin_posts_source, config)

    if src.get("niche_boards", {}).get("enabled", True):
        _collect(all_jobs, niche_boards_source, config)

    logger.info(f"Collection complete: {len(all_jobs)} raw jobs from all sources")
    return all_jobs


def _collect(jobs_list: List[Job], fn, *args, **kwargs) -> None:
    """Call a source function and append results, catching all exceptions."""
    try:
        found = fn(*args, **kwargs)
        jobs_list.extend(found)
    except Exception as e:
        logger.error(f"Source {fn.__name__} failed: {e}", exc_info=True)


# ─────────────────────────────────────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(config: dict) -> None:
    start = time.time()
    logger.info("=" * 60)
    logger.info(f"Job Discovery Agent — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    logger.info("=" * 60)

    # ── Stage 0: Check env vars ───────────────────────────────────────────────
    check_env(config)

    # ── Stage 1: Load IND register ────────────────────────────────────────────
    logger.info("Stage 1: Loading IND register…")
    ind_companies = load_ind_companies(config)
    logger.info(f"  → {len(ind_companies)} IND-eligible NL companies loaded")

    # ── Stage 2: Collect raw jobs ─────────────────────────────────────────────
    logger.info("Stage 2: Collecting jobs from all sources…")
    raw_jobs = collect_jobs(config, ind_companies)

    # ── Stage 3: Deduplicate ──────────────────────────────────────────────────
    logger.info("Stage 3: Deduplicating…")
    jobs = deduplicate(raw_jobs)

    # ── Stage 4: Filter seen jobs ─────────────────────────────────────────────
    logger.info("Stage 4: Filtering already-seen jobs…")
    jobs = filter_new(jobs)
    logger.info(f"  → {len(jobs)} new jobs after seen filter")

    if not jobs:
        logger.info("No new jobs found this run. Exiting.")
        return

    # ── Stage 5: Location filter ──────────────────────────────────────────────
    logger.info("Stage 5: Filtering by location…")
    jobs = filter_by_location(jobs, config)

    # ── Stage 6: IND tier tagging ─────────────────────────────────────────────
    logger.info("Stage 6: Tagging IND tiers…")
    jobs = tag_ind_tier(jobs, ind_companies, config)

    # ── Stage 7: GPT disqualifier filter ──────────────────────────────────────
    logger.info("Stage 7: Running LLM disqualifier filter…")
    jobs = run_disqualifier_filter(jobs, config)

    # ── Stage 8: Enrichment ───────────────────────────────────────────────────
    logger.info("Stage 8: Enriching recruiter/HM contacts…")
    jobs = backfill_salary_hints(jobs)
    jobs = enrich_jobs(jobs, config)

    # ── Stage 9: Outputs ──────────────────────────────────────────────────────
    logger.info("Stage 9: Writing outputs…")
    active = [j for j in jobs if j.tier != "disqualified"]
    tier1  = [j for j in active if j.tier == "tier1"]
    tier2  = [j for j in active if j.tier == "tier2"]
    disq   = [j for j in jobs if j.tier == "disqualified"]

    logger.info(f"  Summary: {len(tier1)} Tier 1 | {len(tier2)} Tier 2 | {len(disq)} Disqualified")

    batch_path = write_batch_tsv(active, config)

    if config["outputs"].get("github_push") and batch_path:
        push_batch_to_github(batch_path, config)

    save_run_log(jobs)

    # ── Stage 10: Mark as seen ────────────────────────────────────────────────
    logger.info("Stage 10: Persisting seen job IDs…")
    mark_seen(jobs)

    elapsed = time.time() - start
    logger.info(f"Pipeline complete in {elapsed:.1f}s")
    logger.info(f"Next step: pull job-hunter-agent repo, open Claude Code, run /career-ops batch")
    logger.info("=" * 60)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if Path(".env").exists():
        load_dotenv()
    elif Path("../.env").exists():
        load_dotenv("../.env")

    config = load_config()
    run_pipeline(config)