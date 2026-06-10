"""
models.py — Core Job data model used across the entire pipeline.
"""

from dataclasses import dataclass, field
from datetime import datetime
import hashlib


@dataclass
class Job:
    # ── Required ───────────────────────────────────────────────────────────────
    url: str
    title: str = ""
    company: str = ""
    location: str = ""
    source: str = ""          # adzuna | serper | wellfound | relocateme |
                              # undutchables | gmail_yutori | gmail_andrew |
                              # greenhouse | lever | ashby | portal_static | private

    # ── Description (truncated before storage, full used for LLM calls) ───────
    description: str = ""
    date_posted: str = ""
    salary_hint: str = ""

    # ── Visa Tier ──────────────────────────────────────────────────────────────
    # tier1        = IND-registered company  → direct sponsorship route
    # tier2        = not IND-registered, no explicit block → PAYSE route
    # disqualified = explicit EU-only / no sponsorship language found
    # unknown      = not yet classified
    tier: str = "unknown"
    ind_registered: bool = False
    disqualifier_found: bool = False
    disqualifier_reason: str = ""

    # ── Recruiter / HM enrichment ──────────────────────────────────────────────
    recruiter_name: str = ""
    recruiter_linkedin: str = ""
    hm_hint: str = ""

    # ── Metadata ───────────────────────────────────────────────────────────────
    date_found: str = field(default_factory=lambda: datetime.utcnow().strftime("%Y-%m-%d"))

    # ── Derived ────────────────────────────────────────────────────────────────
    @property
    def job_id(self) -> str:
        """Stable 12-char hash of URL — used for deduplication."""
        return hashlib.md5(self.url.encode()).hexdigest()[:12]

    def to_dict(self, truncate_desc: bool = True) -> dict:
        desc = self.description[:600] + "…" if truncate_desc and len(self.description) > 600 else self.description
        return {
            "id": self.job_id,
            "url": self.url,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "source": self.source,
            "description": desc,
            "date_posted": self.date_posted,
            "salary_hint": self.salary_hint,
            "tier": self.tier,
            "ind_registered": self.ind_registered,
            "disqualifier_found": self.disqualifier_found,
            "disqualifier_reason": self.disqualifier_reason,
            "recruiter_name": self.recruiter_name,
            "recruiter_linkedin": self.recruiter_linkedin,
            "hm_hint": self.hm_hint,
            "date_found": self.date_found,
        }

    def to_career_ops_entry(self) -> dict:
        """Format expected by career-ops batch YAML."""
        return {
            "url": self.url,
            "company": self.company,
            "title": self.title,
            "tier": self.tier,
            "recruiter": self.recruiter_name or "unknown",
            "recruiter_linkedin": self.recruiter_linkedin or "",
            "notes": f"Source: {self.source} | IND: {self.ind_registered} | Salary: {self.salary_hint or 'unknown'}",
        }
