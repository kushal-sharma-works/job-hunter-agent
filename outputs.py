"""
outputs.py — All output writers for the discovery pipeline.

1. write_batch_tsv   → batches/batch-input-YYYY-MM-DD.tsv  (career-ops /batch input)
2. push_batch_to_github → push TSV to GitHub repo via Contents API
3. save_run_log      → run_log_YYYY-MM-DD.json  (debugging / audit trail)
"""

import csv
import json
import logging
import os
from base64 import b64encode
from datetime import datetime
from pathlib import Path
from typing import List

import requests

from models import Job

logger = logging.getLogger(__name__)

TODAY = datetime.utcnow().strftime("%Y-%m-%d")


# ─────────────────────────────────────────────────────────────────────────────
# 1. Batch TSV — career-ops /batch command input
# ─────────────────────────────────────────────────────────────────────────────

def write_batch_tsv(jobs: List[Job], config: dict) -> Path:
    """
    Write batch-input-YYYY-MM-DD.tsv for the career-ops /batch command.
    Columns: id, url, source, notes
    Only non-disqualified jobs are included.
    """
    batch_dir = Path(config["outputs"].get("batch_dir", "batches"))
    batch_dir.mkdir(parents=True, exist_ok=True)
    output_path = batch_dir / f"batch-input-{TODAY}.tsv"

    active = [j for j in jobs if j.tier != "disqualified"]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["id", "url", "source", "notes"])
        for i, job in enumerate(active, start=1):
            note = f"{job.tier} | {job.title} @ {job.company}"
            if job.salary_hint:
                note += f" | {job.salary_hint}"
            writer.writerow([i, job.url, job.source, note])

    logger.info(f"Batch TSV written: {output_path} ({len(active)} jobs)")
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# 2. Push TSV to GitHub repo
# ─────────────────────────────────────────────────────────────────────────────

def push_batch_to_github(batch_path: Path, config: dict) -> bool:
    """
    Push the batch TSV to the job-hunter-agent GitHub repo using the GitHub
    Contents API. Requires GITHUB_PAT environment variable with repo write access.
    """
    token = os.environ.get("GITHUB_PAT", "")
    if not token:
        logger.warning("GITHUB_PAT not set — skipping GitHub push.")
        return False

    repo = config["outputs"]["career_ops_repo"]
    batch_dir = config["outputs"].get("batch_dir", "batches")
    remote_path = f"{batch_dir}/{batch_path.name}"
    api_url = f"https://api.github.com/repos/{repo}/contents/{remote_path}"

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    content_b64 = b64encode(batch_path.read_bytes()).decode()

    # Check if file already exists (needed to get sha for update)
    sha = None
    try:
        r = requests.get(api_url, headers=headers, timeout=15)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception:
        pass

    payload = {
        "message": f"chore: add job batch {TODAY}",
        "content": content_b64,
    }
    if sha:
        payload["sha"] = sha

    try:
        r = requests.put(api_url, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        logger.info(f"GitHub push: {remote_path} → {repo} ✓")
        return True
    except Exception as e:
        logger.error(f"GitHub push failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 3. Run log (JSON) — for debugging and auditing
# ─────────────────────────────────────────────────────────────────────────────

def save_run_log(jobs: List[Job]) -> Path:
    log_path = Path(f"run_log_{TODAY}.json")
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump([j.to_dict() for j in jobs], f, ensure_ascii=False, indent=2)
    logger.info(f"Run log saved: {log_path}")
    return log_path