"""
sources.py — All job source integrations.

Each source returns List[Job]. Errors are caught and logged — a failing
source never kills the whole run.
"""

import json
import logging
import os
import re
import time
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlencode, urljoin

import requests
import yaml
from bs4 import BeautifulSoup

from models import Job

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}


def _get(url: str, params: dict = None, headers: dict = None, timeout: int = 15) -> Optional[requests.Response]:
    try:
        h = {**HEADERS, **(headers or {})}
        r = requests.get(url, params=params, headers=h, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception as e:
        logger.warning(f"GET failed: {url} → {e}")
        return None


def _load_config() -> dict:
    with open("config.yml") as f:
        return yaml.safe_load(f)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Adzuna API
# ─────────────────────────────────────────────────────────────────────────────

def adzuna_jobs(keywords: List[str], config: dict) -> List[Job]:
    """Adzuna public API — free tier, 250 req/day."""
    app_id = os.environ["ADZUNA_APP_ID"]
    app_key = os.environ["ADZUNA_APP_KEY"]
    rpp = config["sources"]["adzuna"].get("results_per_keyword", 50)
    jobs = []

    for kw in keywords:
        for page in range(1, 3):  # up to 2 pages per keyword
            params = {
                "app_id": app_id,
                "app_key": app_key,
                "results_per_page": rpp,
                "what": kw,
                "where": "netherlands",
                "content-type": "application/json",
            }
            r = _get(
                f"https://api.adzuna.com/v1/api/jobs/nl/search/{page}",
                params=params,
            )
            if not r:
                break
            data = r.json()
            results = data.get("results", [])
            if not results:
                break
            for item in results:
                jobs.append(Job(
                    url=item.get("redirect_url", ""),
                    title=item.get("title", ""),
                    company=item.get("company", {}).get("display_name", ""),
                    location=item.get("location", {}).get("display_name", ""),
                    description=item.get("description", ""),
                    salary_hint=_adzuna_salary(item),
                    date_posted=item.get("created", "")[:10],
                    source="adzuna",
                ))
        time.sleep(0.5)  # gentle rate limiting

    logger.info(f"Adzuna: {len(jobs)} raw jobs across {len(keywords)} keywords")
    return jobs


def _adzuna_salary(item: dict) -> str:
    lo = item.get("salary_min")
    hi = item.get("salary_max")
    if lo and hi:
        return f"€{int(lo):,}–€{int(hi):,}"
    if lo:
        return f"€{int(lo):,}+"
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# 2. Serper — Google Jobs
# ─────────────────────────────────────────────────────────────────────────────

def serper_jobs(keywords: List[str], config: dict) -> List[Job]:
    """Serper.dev Google Jobs endpoint — 2,500 free req/month."""
    api_key = os.environ["SERPER_API_KEY"]
    rpp = config["sources"]["serper"].get("results_per_keyword", 10)
    jobs = []

    for kw in keywords:
        try:
            r = requests.post(
                "https://google.serper.dev/jobs",
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json={"q": kw, "location": "Netherlands", "num": rpp},
                timeout=15,
            )
            r.raise_for_status()
            for item in r.json().get("jobs", []):
                jobs.append(Job(
                    url=item.get("link", ""),
                    title=item.get("title", ""),
                    company=item.get("company", ""),
                    location=item.get("location", ""),
                    description=item.get("description", ""),
                    date_posted=item.get("date", ""),
                    salary_hint=item.get("salary", ""),
                    source="serper",
                ))
        except Exception as e:
            logger.warning(f"Serper failed for '{kw}': {e}")
        time.sleep(0.3)

    logger.info(f"Serper: {len(jobs)} raw jobs")
    return jobs


# ─────────────────────────────────────────────────────────────────────────────
# 3. Greenhouse public ATS API
# ─────────────────────────────────────────────────────────────────────────────

def greenhouse_jobs(slug: str, company_name: str = "") -> List[Job]:
    """Query Greenhouse public jobs API for a company slug."""
    r = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    if not r:
        return []
    jobs = []
    for item in r.json().get("jobs", []):
        loc = item.get("location", {}).get("name", "")
        # Only NL-based roles
        if not _is_nl_location(loc):
            continue
        jobs.append(Job(
            url=item.get("absolute_url", ""),
            title=item.get("title", ""),
            company=company_name or slug,
            location=loc,
            source="greenhouse",
            date_posted=item.get("updated_at", "")[:10],
        ))
    return jobs


# ─────────────────────────────────────────────────────────────────────────────
# 4. Lever public ATS API
# ─────────────────────────────────────────────────────────────────────────────

def lever_jobs(slug: str, company_name: str = "") -> List[Job]:
    """Query Lever public postings API for a company slug."""
    r = _get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if not r:
        return []
    jobs = []
    for item in r.json():
        loc = item.get("categories", {}).get("location", "")
        if not _is_nl_location(loc):
            continue
        jobs.append(Job(
            url=item.get("hostedUrl", ""),
            title=item.get("text", ""),
            company=company_name or slug,
            location=loc,
            description=item.get("descriptionPlain", ""),
            source="lever",
            date_posted=datetime.fromtimestamp(
                item.get("createdAt", 0) / 1000
            ).strftime("%Y-%m-%d") if item.get("createdAt") else "",
        ))
    return jobs


# ─────────────────────────────────────────────────────────────────────────────
# 5. Ashby public ATS API
# ─────────────────────────────────────────────────────────────────────────────

def ashby_jobs(slug: str, company_name: str = "") -> List[Job]:
    """Query Ashby public job board API for a company slug."""
    try:
        r = requests.post(
            f"https://jobs.ashbyhq.com/api/non-user-facing/job-board/with-pagination",
            json={"organizationHostedJobsPageName": slug, "pageSize": 100, "page": 1},
            timeout=15,
        )
        r.raise_for_status()
        jobs = []
        for item in r.json().get("results", []):
            loc = item.get("secondaryLocations", [{}])[0].get("city", "") or \
                  item.get("primaryLocation", {}).get("city", "")
            country = item.get("primaryLocation", {}).get("country", "")
            if country and country.lower() not in ("netherlands", "nl", "the netherlands"):
                continue
            jobs.append(Job(
                url=f"https://jobs.ashbyhq.com/{slug}/{item.get('id', '')}",
                title=item.get("title", ""),
                company=company_name or slug,
                location=f"{loc}, Netherlands",
                source="ashby",
            ))
        return jobs
    except Exception as e:
        logger.warning(f"Ashby failed for '{slug}': {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# 6. Static portals list (Greenhouse/Lever/Ashby slugs + careers pages)
# ─────────────────────────────────────────────────────────────────────────────

def portals_static_jobs(portals_file: str = "portals.yml") -> List[Job]:
    """Query every company in portals.yml."""
    with open(portals_file) as f:
        portals_config = yaml.safe_load(f)

    jobs = []
    for portal in portals_config.get("portals", []):
        name = portal["name"]
        ptype = portal.get("type", "")
        slug = portal.get("slug", "")

        try:
            if ptype == "greenhouse" and slug:
                found = greenhouse_jobs(slug, name)
            elif ptype == "lever" and slug:
                found = lever_jobs(slug, name)
            elif ptype == "ashby" and slug:
                found = ashby_jobs(slug, name)
            elif ptype == "careers_page" and portal.get("url"):
                found = scrape_careers_page(portal["url"], name)
            else:
                found = []

            logger.debug(f"Portals static [{name}]: {len(found)} jobs")
            jobs.extend(found)
        except Exception as e:
            logger.warning(f"Portals static [{name}] failed: {e}")

        time.sleep(0.3)

    logger.info(f"Portals static: {len(jobs)} raw jobs")
    return jobs


def scrape_careers_page(url: str, company: str) -> List[Job]:
    """Generic scraper for careers pages — extracts any <a> that looks like a job link."""
    r = _get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    jobs = []
    seen_urls = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if not href or not text or len(text) < 8:
            continue
        if not any(kw in href.lower() for kw in ("job", "career", "vacature", "vacancy", "position", "role", "opening")):
            continue
        full_url = href if href.startswith("http") else urljoin(url, href)
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)
        jobs.append(Job(
            url=full_url,
            title=text[:150],
            company=company,
            source="portal_static",
        ))
    return jobs[:30]  # cap — generic scraper isn't precise


# ─────────────────────────────────────────────────────────────────────────────
# 7. Wellfound (Playwright required)
# ─────────────────────────────────────────────────────────────────────────────

def wellfound_jobs(config: dict) -> List[Job]:
    """Scrape Wellfound with Playwright (handles JS rendering)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright not installed — skipping Wellfound. Run: pip install playwright && playwright install chromium")
        return []

    url = config["sources"]["wellfound"].get("url", "https://wellfound.com/role/l/software-engineer/netherlands")
    jobs = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_selector("[data-test='StartupResult']", timeout=15000)

            cards = page.query_selector_all("[data-test='StartupResult']")
            for card in cards[:40]:
                try:
                    job_links = card.query_selector_all("a[href*='/jobs/']")
                    company_el = card.query_selector("[data-test='startup-name']") or \
                                 card.query_selector("h2")
                    company = company_el.inner_text().strip() if company_el else ""

                    for link in job_links[:5]:
                        href = link.get_attribute("href") or ""
                        title = link.inner_text().strip()
                        full_url = href if href.startswith("http") else f"https://wellfound.com{href}"
                        if title and full_url:
                            jobs.append(Job(
                                url=full_url,
                                title=title,
                                company=company,
                                location="Netherlands",
                                source="wellfound",
                            ))
                except Exception:
                    continue

            browser.close()
    except Exception as e:
        logger.warning(f"Wellfound scrape failed: {e}")

    logger.info(f"Wellfound: {len(jobs)} raw jobs")
    return jobs


# ─────────────────────────────────────────────────────────────────────────────
# 8. Relocate.me
# ─────────────────────────────────────────────────────────────────────────────

def relocateme_jobs(config: dict) -> List[Job]:
    url = config["sources"]["relocateme"].get("url", "https://relocate.me/search?q=developer&where=nl")
    r = _get(url)
    if not r:
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    jobs = []

    for card in soup.select("article.job-card, div.job-card, li.job-item")[:50]:
        title_el = card.select_one("h2, h3, .job-title, [class*='title']")
        company_el = card.select_one(".company-name, [class*='company']")
        link_el = card.select_one("a[href]")
        loc_el = card.select_one(".location, [class*='location']")

        if not title_el or not link_el:
            continue

        href = link_el["href"]
        full_url = href if href.startswith("http") else f"https://relocate.me{href}"

        jobs.append(Job(
            url=full_url,
            title=title_el.get_text(strip=True),
            company=company_el.get_text(strip=True) if company_el else "",
            location=loc_el.get_text(strip=True) if loc_el else "Netherlands",
            source="relocateme",
        ))

    logger.info(f"Relocate.me: {len(jobs)} raw jobs")
    return jobs


# ─────────────────────────────────────────────────────────────────────────────
# 9. Undutchables
# ─────────────────────────────────────────────────────────────────────────────

def undutchables_jobs(config: dict) -> List[Job]:
    url = config["sources"]["undutchables"].get(
        "url", "https://www.undutchables.nl/vacatures/?function=it-and-telecom"
    )
    jobs = []

    for page_num in range(1, 5):  # up to 4 pages
        paged_url = f"{url}&pagina={page_num}" if page_num > 1 else url
        r = _get(paged_url)
        if not r:
            break

        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select("article.vacancy, li.vacancy-item, div.job-listing")
        if not cards:
            break

        for card in cards:
            link_el = card.select_one("a[href]")
            title_el = card.select_one("h2, h3, .vacancy-title")
            company_el = card.select_one(".company, .employer")
            loc_el = card.select_one(".location, .city")

            if not link_el or not title_el:
                continue

            href = link_el["href"]
            full_url = href if href.startswith("http") else f"https://www.undutchables.nl{href}"

            jobs.append(Job(
                url=full_url,
                title=title_el.get_text(strip=True),
                company=company_el.get_text(strip=True) if company_el else "",
                location=loc_el.get_text(strip=True) if loc_el else "Netherlands",
                source="undutchables",
            ))

        time.sleep(1)

    logger.info(f"Undutchables: {len(jobs)} raw jobs")
    return jobs


# ─────────────────────────────────────────────────────────────────────────────
# 10. Gmail — Yutori Scouts label + Andrew Stutanski weekly email
# ─────────────────────────────────────────────────────────────────────────────

def gmail_jobs(config: dict) -> List[Job]:
    """Extract job URLs from Gmail using Gmail API."""
    try:
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials
    except ImportError:
        logger.warning("google-api-python-client not installed — skipping Gmail source.")
        return []

    token_file = os.environ.get("GMAIL_TOKEN_FILE", "gmail_token.json")
    if not os.path.exists(token_file):
        logger.warning("Gmail token not found — run gmail_setup.py first. Skipping Gmail source.")
        return []

    creds = Credentials.from_authorized_user_file(token_file)
    service = build("gmail", "v1", credentials=creds)
    jobs = []

    # ── Yutori Scouts label ──────────────────────────────────────────────────
    if config["sources"]["gmail_yutori"]["enabled"]:
        label = config["sources"]["gmail_yutori"]["label"]
        try:
            jobs.extend(_extract_jobs_from_gmail_query(
                service, f'label:"{label}" newer_than:7d', source="gmail_yutori"
            ))
        except Exception as e:
            logger.warning(f"Gmail Yutori failed: {e}")

    # ── Andrew Stutanski curated email ───────────────────────────────────────
    if config["sources"]["gmail_andrew"]["enabled"]:
        sender = config["sources"]["gmail_andrew"]["sender_contains"]
        try:
            jobs.extend(_extract_jobs_from_gmail_query(
                service, f'from:{sender} newer_than:10d', source="gmail_andrew"
            ))
        except Exception as e:
            logger.warning(f"Gmail Andrew failed: {e}")

    logger.info(f"Gmail: {len(jobs)} raw jobs")
    return jobs


def _extract_jobs_from_gmail_query(service, query: str, source: str) -> List[Job]:
    """Search Gmail and extract all job URLs from matching emails."""
    result = service.users().messages().list(userId="me", q=query, maxResults=20).execute()
    messages = result.get("messages", [])
    jobs = []
    url_pattern = re.compile(
        r'https?://[^\s"\'<>]+(?:job|career|vacature|vacancy|position|opening)[^\s"\'<>]*',
        re.IGNORECASE,
    )

    for msg_ref in messages:
        try:
            msg = service.users().messages().get(
                userId="me", id=msg_ref["id"], format="full"
            ).execute()
            body = _gmail_body(msg)
            urls = set(url_pattern.findall(body))
            for url in urls:
                # Best-effort title from URL slug
                slug = url.rstrip("/").split("/")[-1].replace("-", " ").title()
                jobs.append(Job(
                    url=url,
                    title=slug[:100],
                    source=source,
                ))
        except Exception:
            continue

    return jobs


def _gmail_body(message: dict) -> str:
    """Decode all text parts from a Gmail message."""
    import base64
    parts = message.get("payload", {}).get("parts", [])
    if not parts:
        data = message.get("payload", {}).get("body", {}).get("data", "")
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore") if data else ""

    texts = []
    for part in parts:
        mime = part.get("mimeType", "")
        if mime in ("text/plain", "text/html"):
            data = part.get("body", {}).get("data", "")
            if data:
                decoded = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
                texts.append(decoded)
    return "\n".join(texts)


# ─────────────────────────────────────────────────────────────────────────────
# 11. Private portal stub
# ─────────────────────────────────────────────────────────────────────────────

def private_portal_jobs(config: dict) -> List[Job]:
    """
    Stub for the course teacher's private job portal.

    TODO once access method is known:
    - If login-based website → implement Playwright login + scrape
    - If email → add a Gmail query pattern here
    - If API → add API call

    Set sources.private_portal.enabled: true in config.yml once implemented.
    """
    logger.info("Private portal: stub not yet implemented")
    return []


# ─────────────────────────────────────────────────────────────────────────────
# 12. IND-driven ATS batch query
# ─────────────────────────────────────────────────────────────────────────────

def ind_ats_jobs(ind_companies: List[str], config: dict) -> List[Job]:
    """
    For each IND-registered company, attempt to find their jobs via
    Greenhouse, Lever, and Ashby public APIs by guessing their slug.

    Slug guessing strategy: normalise company name → try 3–5 variations.
    Hit rate ~25–35% but completely free and automatic.
    """
    jobs = []
    checked = set()

    for company in ind_companies:
        slugs = _guess_slugs(company)
        for slug in slugs:
            if slug in checked:
                continue
            checked.add(slug)

            if config["sources"].get("greenhouse_ind", {}).get("enabled", True):
                found = greenhouse_jobs(slug, company)
                if found:
                    logger.debug(f"IND Greenhouse hit: {company} → {slug} ({len(found)} jobs)")
                    jobs.extend(found)
                    break  # found on Greenhouse, skip Lever/Ashby

            if config["sources"].get("lever_ind", {}).get("enabled", True):
                found = lever_jobs(slug, company)
                if found:
                    logger.debug(f"IND Lever hit: {company} → {slug} ({len(found)} jobs)")
                    jobs.extend(found)
                    break

            if config["sources"].get("ashby_ind", {}).get("enabled", True):
                found = ashby_jobs(slug, company)
                if found:
                    logger.debug(f"IND Ashby hit: {company} → {slug} ({len(found)} jobs)")
                    jobs.extend(found)
                    break

        time.sleep(0.15)

    logger.info(f"IND ATS: {len(jobs)} jobs from {len(ind_companies)} companies")
    return jobs


def _guess_slugs(company_name: str) -> List[str]:
    """Generate likely ATS slug variations from a company name."""
    import re
    # Strip legal suffixes
    name = re.sub(r"\b(B\.?V\.?|N\.?V\.?|BV|NV|Ltd|LLC|Inc|GmbH|SE|Holding|Group)\b", "", company_name, flags=re.IGNORECASE)
    name = name.strip(" .,")

    base = re.sub(r"[^a-zA-Z0-9\s]", "", name).strip()
    slug1 = base.lower().replace(" ", "")       # "bookingcom"
    slug2 = base.lower().replace(" ", "-")     # "booking-com"
    slug3 = base.lower().split()[0] if base.split() else slug1  # first word only

    return list(dict.fromkeys([slug1, slug2, slug3]))  # deduplicated, ordered


# ─────────────────────────────────────────────────────────────────────────────
# Utility
# ─────────────────────────────────────────────────────────────────────────────

NL_LOCATION_KEYWORDS = {
    "amsterdam", "rotterdam", "utrecht", "hague", "den haag", "eindhoven",
    "delft", "leiden", "haarlem", "almere", "netherlands", "nederland",
    "nl", "schiphol",
}


def _is_nl_location(location: str) -> bool:
    """Return True if location string looks like it's in the Netherlands."""
    if not location:
        return False  # no location info — include by default (filtered later)
    loc_lower = location.lower()
    return any(kw in loc_lower for kw in NL_LOCATION_KEYWORDS)
