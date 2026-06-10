"""
state.py — Deduplication and run-state management.

Persists seen job IDs in seen_jobs.json so re-runs don't reprocess
the same listings. The file lives in the repo root locally and in
the GitHub Actions workspace in CI.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

from models import Job

STATE_FILE = Path(os.getenv("STATE_FILE", "seen_jobs.json"))
MAX_AGE_DAYS = 90  # Prune entries older than this


def _load() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"seen": {}}  # {job_id: date_found}


def _save(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _prune(state: dict) -> dict:
    """Remove entries older than MAX_AGE_DAYS to keep the file lean."""
    cutoff = (datetime.utcnow() - timedelta(days=MAX_AGE_DAYS)).strftime("%Y-%m-%d")
    state["seen"] = {
        jid: date
        for jid, date in state["seen"].items()
        if date >= cutoff
    }
    return state


def is_new(job: Job) -> bool:
    """Return True if this job has NOT been seen before."""
    state = _load()
    return job.job_id not in state["seen"]


def mark_seen(jobs: List[Job]) -> None:
    """Persist a batch of job IDs as seen."""
    state = _load()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    for job in jobs:
        state["seen"][job.job_id] = today
    state = _prune(state)
    _save(state)


def filter_new(jobs: List[Job]) -> List[Job]:
    """Return only jobs not previously seen. Side-effect-free — call mark_seen separately."""
    state = _load()
    return [j for j in jobs if j.job_id not in state["seen"]]


def seen_count() -> int:
    return len(_load()["seen"])
