"""
outputs.py — All output writers for the discovery pipeline.

1. Markdown digest   →  jobs_YYYY-MM-DD.md
2. career-ops batch  →  batch_YYYY-MM-DD.yaml  (pushed to career-ops GitHub repo)
3. Email digest      →  sent to configured Gmail recipient
"""

import json
import logging
import os
import smtplib
import time
from base64 import b64encode
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import List

import requests
import yaml

from models import Job

logger = logging.getLogger(__name__)

TODAY = datetime.utcnow().strftime("%Y-%m-%d")


# ─────────────────────────────────────────────────────────────────────────────
# 1. Markdown digest
# ─────────────────────────────────────────────────────────────────────────────

TIER_EMOJI = {"tier1": "🟢", "tier2": "🟡", "disqualified": "🔴", "unknown": "⚪"}
TIER_LABEL = {"tier1": "Tier 1 — IND Direct", "tier2": "Tier 2 — PAYSE Route", "disqualified": "Disqualified", "unknown": "Unclassified"}


def write_markdown_digest(jobs: List[Job], config: dict) -> Path:
    filename = config["outputs"]["digest_file"].replace("{date}", TODAY)
    output_path = Path(filename)

    active = [j for j in jobs if j.tier != "disqualified"]
    tier1 = [j for j in active if j.tier == "tier1"]
    tier2 = [j for j in active if j.tier == "tier2"]
    disq = [j for j in jobs if j.tier == "disqualified"]

    lines = [
        f"# Job Discovery Digest — {TODAY}",
        "",
        f"**Run stats:** {len(jobs)} raw → {len(active)} active ({len(tier1)} Tier 1, {len(tier2)} Tier 2) | {len(disq)} disqualified",
        "",
        "---",
        "",
    ]

    for tier_key, tier_jobs in [("tier1", tier1), ("tier2", tier2)]:
        if not tier_jobs:
            continue
        emoji = TIER_EMOJI[tier_key]
        label = TIER_LABEL[tier_key]
        lines.append(f"## {emoji} {label} ({len(tier_jobs)} jobs)")
        lines.append("")
        for job in tier_jobs:
            lines.extend(_job_to_md_block(job))
        lines.append("")

    if disq:
        lines.append(f"## {TIER_EMOJI['disqualified']} Disqualified ({len(disq)} jobs)")
        lines.append("")
        for job in disq:
            lines.append(f"- ~~[{job.title} @ {job.company}]({job.url})~~ — {job.disqualifier_reason}")
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Markdown digest written: {output_path}")
    return output_path


def _job_to_md_block(job: Job) -> List[str]:
    lines = [
        f"### [{job.title}]({job.url})",
        f"**{job.company}** · {job.location}",
    ]
    meta_parts = []
    if job.source:
        meta_parts.append(f"Source: `{job.source}`")
    if job.date_posted:
        meta_parts.append(f"Posted: {job.date_posted}")
    if job.salary_hint:
        meta_parts.append(f"Salary: {job.salary_hint}")
    if meta_parts:
        lines.append(" | ".join(meta_parts))

    if job.recruiter_name:
        recruiter_line = f"👤 Recruiter: **{job.recruiter_name}**"
        if job.recruiter_linkedin:
            recruiter_line += f" · [LinkedIn]({job.recruiter_linkedin})"
        lines.append(recruiter_line)
    if job.hm_hint:
        lines.append(f"🏢 HM hint: {job.hm_hint}")

    if job.description:
        snippet = job.description[:280].replace("\n", " ").strip()
        if len(job.description) > 280:
            snippet += "…"
        lines.append(f"> {snippet}")

    lines.append("")
    return lines


# ─────────────────────────────────────────────────────────────────────────────
# 2. career-ops batch YAML
# ─────────────────────────────────────────────────────────────────────────────

def write_batch_yaml(jobs: List[Job], config: dict) -> Path:
    """
    Write a batch YAML file in the format career-ops expects.
    Only includes non-disqualified jobs.
    """
    filename = config["outputs"]["batch_file"].replace("{date}", TODAY)
    output_path = Path(filename)

    active = [j for j in jobs if j.tier != "disqualified"]
    batch_entries = [j.to_career_ops_entry() for j in active]

    batch_data = {
        "metadata": {
            "generated": TODAY,
            "total_jobs": len(active),
            "tier1_count": sum(1 for j in active if j.tier == "tier1"),
            "tier2_count": sum(1 for j in active if j.tier == "tier2"),
            "min_score_for_pdf": config.get("career_ops", {}).get("min_score_for_pdf", 4.0),
        },
        "jobs": batch_entries,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(batch_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    logger.info(f"Batch YAML written: {output_path} ({len(active)} jobs)")
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# 3. Push batch YAML to career-ops GitHub repo
# ─────────────────────────────────────────────────────────────────────────────

def push_batch_to_github(batch_path: Path, config: dict) -> bool:
    """
    Push the batch YAML to the career-ops GitHub repo using the GitHub Contents API.
    Requires GITHUB_PAT environment variable with repo write access.
    """
    token = os.environ.get("GITHUB_PAT", "")
    if not token:
        logger.warning("GITHUB_PAT not set — skipping GitHub push.")
        return False

    repo = config["outputs"]["career_ops_repo"]
    batch_dir = config["outputs"].get("career_ops_batch_dir", "batches")
    remote_path = f"{batch_dir}/{batch_path.name}"

    api_url = f"https://api.github.com/repos/{repo}/contents/{remote_path}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    content_b64 = b64encode(batch_path.read_bytes()).decode()

    # Check if file already exists (needed for update, to get the sha)
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
# 4. Email digest (Gmail SMTP via App Password)
# ─────────────────────────────────────────────────────────────────────────────

def send_email_digest(jobs: List[Job], markdown_path: Path, config: dict) -> bool:
    """
    Send the digest as an HTML email via Gmail SMTP.
    Requires GMAIL_ADDRESS and GMAIL_APP_PASSWORD environment variables.
    """
    sender = os.environ.get("GMAIL_ADDRESS", "")
    password = os.environ.get("GMAIL_APP_PASSWORD", "")
    recipient = config["outputs"]["email_recipient"]

    if not sender or not password:
        logger.warning("GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set — skipping email digest.")
        return False

    active = [j for j in jobs if j.tier != "disqualified"]
    tier1 = [j for j in active if j.tier == "tier1"]
    tier2 = [j for j in active if j.tier == "tier2"]

    subject = f"🔍 Job Digest {TODAY} — {len(tier1)} Tier 1, {len(tier2)} Tier 2"
    html_body = _build_email_html(tier1, tier2, jobs)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(markdown_path.read_text(encoding="utf-8"), "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(sender, password)
            smtp.sendmail(sender, recipient, msg.as_string())
        logger.info(f"Email digest sent to {recipient}")
        return True
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        return False


def _build_email_html(tier1: List[Job], tier2: List[Job], all_jobs: List[Job]) -> str:
    disq_count = sum(1 for j in all_jobs if j.tier == "disqualified")
    rows_t1 = "".join(_job_to_html_row(j, "tier1") for j in tier1)
    rows_t2 = "".join(_job_to_html_row(j, "tier2") for j in tier2)

    return f"""
<!DOCTYPE html>
<html>
<head>
<style>
  body {{ font-family: -apple-system, sans-serif; font-size: 14px; color: #1a1a1a; max-width: 800px; margin: 0 auto; padding: 20px; }}
  h1 {{ font-size: 20px; margin-bottom: 4px; }}
  .stats {{ color: #666; margin-bottom: 24px; font-size: 13px; }}
  h2 {{ font-size: 16px; border-bottom: 2px solid #e0e0e0; padding-bottom: 6px; margin-top: 32px; }}
  .job {{ border: 1px solid #e8e8e8; border-radius: 8px; padding: 14px 16px; margin-bottom: 12px; }}
  .job-title {{ font-weight: 600; font-size: 15px; }}
  .job-title a {{ color: #0066cc; text-decoration: none; }}
  .job-meta {{ font-size: 12px; color: #666; margin-top: 4px; }}
  .job-recruiter {{ font-size: 12px; color: #2a7a2a; margin-top: 4px; }}
  .job-snippet {{ font-size: 13px; color: #444; margin-top: 8px; border-left: 3px solid #ddd; padding-left: 10px; }}
  .t1 {{ border-left: 4px solid #28a745; }}
  .t2 {{ border-left: 4px solid #ffc107; }}
</style>
</head>
<body>
<h1>🔍 Job Discovery Digest — {TODAY}</h1>
<p class="stats">{len(tier1) + len(tier2)} active jobs ({len(tier1)} Tier 1 IND, {len(tier2)} Tier 2 PAYSE) | {disq_count} disqualified</p>

<h2>🟢 Tier 1 — IND Direct Sponsorship ({len(tier1)} jobs)</h2>
{rows_t1 or '<p style="color:#999">No Tier 1 jobs this run.</p>'}

<h2>🟡 Tier 2 — PAYSE Route ({len(tier2)} jobs)</h2>
{rows_t2 or '<p style="color:#999">No Tier 2 jobs this run.</p>'}

<p style="font-size:12px;color:#999;margin-top:40px">
  Generated by job-discovery-agent · Pull career-ops locally and run /career-ops batch to evaluate.
</p>
</body>
</html>
"""


def _job_to_html_row(job: Job, tier: str) -> str:
    css_class = "t1" if tier == "tier1" else "t2"
    recruiter_html = ""
    if job.recruiter_name:
        li = f"<a href='{job.recruiter_linkedin}'>{job.recruiter_name}</a>" if job.recruiter_linkedin else job.recruiter_name
        recruiter_html = f'<div class="job-recruiter">👤 {li}</div>'
    if job.hm_hint:
        recruiter_html += f'<div class="job-recruiter">🏢 HM hint: {job.hm_hint}</div>'

    snippet = ""
    if job.description:
        s = job.description[:200].replace("\n", " ").strip()
        snippet = f'<div class="job-snippet">{s}…</div>'

    salary = f" · {job.salary_hint}" if job.salary_hint else ""

    return f"""
<div class="job {css_class}">
  <div class="job-title"><a href="{job.url}">{job.title}</a></div>
  <div class="job-meta"><strong>{job.company}</strong> · {job.location}{salary} · <em>{job.source}</em></div>
  {recruiter_html}
  {snippet}
</div>"""


# ─────────────────────────────────────────────────────────────────────────────
# 5. Save run log (JSON) — for debugging and auditing
# ─────────────────────────────────────────────────────────────────────────────

def save_run_log(jobs: List[Job]) -> Path:
    log_path = Path(f"run_log_{TODAY}.json")
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump([j.to_dict() for j in jobs], f, ensure_ascii=False, indent=2)
    logger.info(f"Run log saved: {log_path}")
    return log_path
