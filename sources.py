"""
sources.py — All job source integrations.

Each source returns List[Job]. Errors are caught and logged — a failing
source never kills the whole run.

Sources:
  1.  adzuna_jobs           — Adzuna API (free, 250 req/day)
  2.  jobspy_jobs           — python-jobspy: LinkedIn, Indeed, Glassdoor (no key needed)
  3.  greenhouse_jobs       — Greenhouse ATS public API
  4.  lever_jobs            — Lever ATS public API
  5.  ashby_jobs            — Ashby ATS public API
  6.  wellfound_jobs        — Wellfound Playwright scraper
  7.  undutchables_jobs     — Undutchables BS4 scraper
  8.  gmail_jobs            — Gmail API (Yutori label + Andrew Stutanski)
  9.  ind_ats_jobs          — IND register → Greenhouse/Lever/Ashby slug lookup
  10. linkedin_jobs_source  — Brave Search: site:linkedin.com/jobs
  11. linkedin_posts_source — Brave Search: site:linkedin.com/posts + GPT filter
  12. niche_boards_source   — Brave Search: niche NL boards, PM boards,
                              Dutch boards, recruiter sites
"""

import json
import logging
import os
import re
import time
from datetime import datetime
from typing import List, Optional
from urllib.parse import urljoin

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


def _serper_search(query: str, api_key: str, count: int = 10) -> list:
    """
    Call Serper Google Search API. Returns list of result dicts with
    'url', 'title', 'description' keys (normalised from Serper's 'link'/'snippet').
    """
    try:
        r = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "gl": "nl", "num": count, "tbs": "qdr:w3"},
            timeout=15,
        )
        r.raise_for_status()
        results = []
        for item in r.json().get("organic", []):
            results.append({
                "url": item.get("link", ""),
                "title": item.get("title", ""),
                "description": item.get("snippet", ""),
            })
        return results
    except Exception as e:
        logger.warning(f"Serper search failed for '{query[:60]}': {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# 1. Adzuna API
# ─────────────────────────────────────────────────────────────────────────────

def adzuna_jobs(keywords: List[str], config: dict) -> List[Job]:
    """Adzuna public API — free tier, 250 req/day."""
    app_id = os.environ.get("ADZUNA_APP_ID", "")
    app_key = os.environ.get("ADZUNA_APP_KEY", "")
    if not app_id or not app_key:
        logger.warning("ADZUNA_APP_ID / ADZUNA_APP_KEY not set — skipping Adzuna")
        return []

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
        time.sleep(0.5)

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
# 2. python-jobspy — LinkedIn, Indeed, Glassdoor (no API key needed)
# ─────────────────────────────────────────────────────────────────────────────

def jobspy_jobs(keywords: List[str], config: dict) -> List[Job]:
    """
    python-jobspy scrapes LinkedIn, Indeed, and Glassdoor directly.
    No API key required. Replaces Serper Google Jobs endpoint.
    """
    try:
        from jobspy import scrape_jobs
    except ImportError:
        logger.warning("python-jobspy not installed — run: pip install python-jobspy")
        return []

    rpp = config["sources"].get("jobspy", {}).get("results_per_keyword", 15)
    capped_kw = keywords[:6]  # cap to preserve time/bandwidth
    jobs = []

    for kw in capped_kw:
        try:
            df = scrape_jobs(
                site_name=["linkedin", "indeed", "glassdoor"],
                search_term=kw,
                location="Netherlands",
                results_wanted=rpp,
                hours_old=168,  # 7 days
                country_indeed="Netherlands",
            )
            for _, row in df.iterrows():
                url = str(row.get("job_url", ""))
                if not url or url == "nan":
                    continue
                jobs.append(Job(
                    url=url,
                    title=str(row.get("title", "")),
                    company=str(row.get("company", "")),
                    location=str(row.get("location", "")),
                    description=str(row.get("description", ""))[:1000],
                    date_posted=str(row.get("date_posted", "")),
                    salary_hint=str(row.get("min_amount", "") or ""),
                    source="jobspy",
                ))
        except Exception as e:
            logger.warning(f"jobspy failed for '{kw}': {e}")
        time.sleep(2)  # jobspy scrapes directly — be polite

    logger.info(f"JobSpy: {len(jobs)} raw jobs")
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
            "https://jobs.ashbyhq.com/api/non-user-facing/job-board/with-pagination",
            json={"organizationHostedJobsPageName": slug, "pageSize": 100, "page": 1},
            timeout=15,
        )
        r.raise_for_status()
        jobs = []
        for item in r.json().get("results", []):
            loc = (
                item.get("secondaryLocations", [{}])[0].get("city", "")
                or item.get("primaryLocation", {}).get("city", "")
            )
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
# 6. Wellfound (Playwright required)
# ─────────────────────────────────────────────────────────────────────────────

def wellfound_jobs(config: dict) -> List[Job]:
    """Scrape Wellfound with Playwright (handles JS rendering)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright not installed — skipping Wellfound. Run: pip install playwright && playwright install chromium")
        return []

    url = config["sources"]["wellfound"].get(
        "url", "https://wellfound.com/role/l/software-engineer/netherlands"
    )
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
                    company_el = (
                        card.query_selector("[data-test='startup-name']")
                        or card.query_selector("h2")
                    )
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
# 7. Undutchables
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
# 8. Gmail — Yutori Scouts label + Andrew Stutanski weekly email
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
                service, f"from:{sender} newer_than:10d", source="gmail_andrew"
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
# 9. IND-driven ATS batch query
# ─────────────────────────────────────────────────────────────────────────────

def ind_ats_jobs(ind_companies: List[str], config: dict) -> List[Job]:
    """
    For each IND-registered company, attempt to find their jobs via
    Greenhouse, Lever, and Ashby public APIs by guessing their slug.
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
                    break

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
    name = re.sub(
        r"\b(B\.?V\.?|N\.?V\.?|BV|NV|Ltd|LLC|Inc|GmbH|SE|Holding|Group)\b",
        "", company_name, flags=re.IGNORECASE
    )
    name = name.strip(" .,")
    base = re.sub(r"[^a-zA-Z0-9\s]", "", name).strip()
    slug1 = base.lower().replace(" ", "")     # "bookingcom"
    slug2 = base.lower().replace(" ", "-")    # "booking-com"
    slug3 = base.lower().split()[0] if base.split() else slug1  # first word
    return list(dict.fromkeys([slug1, slug2, slug3]))


# ─────────────────────────────────────────────────────────────────────────────
# 10. LinkedIn job listings (Brave Search, site: filter)
# ─────────────────────────────────────────────────────────────────────────────

def linkedin_jobs_source(keywords: List[str], config: dict) -> List[Job]:
    """Brave Search against linkedin.com/jobs."""
    api_key = os.environ.get("SERPER_API_KEY", "")
    if not api_key:
        logger.warning("SERPER_API_KEY not set — skipping linkedin_jobs")
        return []

    jobs = []
    capped_kw = keywords[:6]

    for kw in capped_kw:
        query = f'site:linkedin.com/jobs "{kw}" "Netherlands" "visa sponsorship"'
        for item in _serper_search(query, api_key, count=10):
            url = item.get("url", "")
            if "linkedin.com/jobs" not in url:
                continue
            raw_title = item.get("title", "")
            title = raw_title.replace(" - LinkedIn", "").strip()
            company = ""
            if " at " in title:
                parts = title.rsplit(" at ", 1)
                title = parts[0].strip()
                company = parts[1].strip()
            jobs.append(Job(
                url=url,
                title=title,
                company=company,
                location="Netherlands",
                source="linkedin_jobs",
            ))
        time.sleep(0.3)

    logger.info(f"LinkedIn jobs: {len(jobs)} raw jobs")
    return jobs


# ─────────────────────────────────────────────────────────────────────────────
# 11. LinkedIn hiring posts (Brave Search + GPT filter)
# ─────────────────────────────────────────────────────────────────────────────

_LINKEDIN_POST_QUERIES = [
    'site:linkedin.com/posts "visa sponsorship" "hiring" "Netherlands" developer',
    'site:linkedin.com/posts "visa sponsorship" "hiring" "Amsterdam" engineer',
    'site:linkedin.com/posts "kennismigrant" "hiring" developer',
    'site:linkedin.com/posts "relocation" "hiring" "Netherlands" "product manager"',
    'site:linkedin.com/posts "we are hiring" "visa" "Netherlands" tech',
]


def linkedin_posts_source(config: dict) -> List[Job]:
    """Brave Search against linkedin.com/posts — catches direct hiring announcements."""
    api_key = os.environ.get("SERPER_API_KEY", "")
    if not api_key:
        logger.warning("SERPER_API_KEY not set — skipping linkedin_posts")
        return []

    raw_posts = []
    for query in _LINKEDIN_POST_QUERIES:
        for item in _serper_search(query, api_key, count=10):
            url = item.get("url", "")
            if "linkedin.com/posts" not in url:
                continue
            raw_posts.append({
                "url": url,
                "title": item.get("title", ""),
                "snippet": item.get("description", ""),
            })
        time.sleep(0.3)

    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_key and raw_posts:
        filtered = _filter_posts_with_gpt(raw_posts, openai_key)
    else:
        filtered = raw_posts

    jobs = []
    for post in filtered:
        jobs.append(Job(
            url=post["url"],
            title=post.get("role", post["title"][:80]),
            company=post.get("company", ""),
            location="Netherlands",
            source="linkedin_posts",
        ))

    logger.info(f"LinkedIn posts: {len(jobs)} jobs after filter")
    return jobs


def _filter_posts_with_gpt(posts: list, api_key: str) -> list:
    """
    GPT-4o-mini filter — keeps only genuine job openings with NL visa/relocation support.
    Returns filtered list; falls back to all posts on any failure.
    """
    import json as _json
    try:
        import openai
        client = openai.OpenAI(api_key=api_key)

        snippets = [
            {"id": i, "title": p["title"], "snippet": p["snippet"]}
            for i, p in enumerate(posts)
        ]
        system = (
            "You are a filter. Given a list of LinkedIn post snippets, "
            "return ONLY those that are genuine job openings with visa sponsorship "
            "or relocation support in the Netherlands. "
            'Respond with ONLY a JSON array: [{"id": N, "role": "...", "company": "..."}]. '
            "No preamble, no markdown."
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": _json.dumps(snippets)},
            ],
            max_tokens=600,
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        filtered_ids = {item["id"]: item for item in _json.loads(raw)}
        result = []
        for i, post in enumerate(posts):
            if i in filtered_ids:
                post = dict(post)
                post["role"] = filtered_ids[i].get("role", "")
                post["company"] = filtered_ids[i].get("company", "")
                result.append(post)
        return result
    except Exception as e:
        logger.warning(f"LinkedIn posts GPT filter failed — passing all through: {e}")
        return posts


# ─────────────────────────────────────────────────────────────────────────────
# 12. Niche boards — tech boards, PM boards, Dutch boards, recruiter sites
# ─────────────────────────────────────────────────────────────────────────────

_NICHE_BOARD_QUERIES = [
    # ── Tech-specific NL boards ──────────────────────────────────────────────
    'site:devitjobs.nl Netherlands',
    'site:iamexpat.nl "visa sponsorship" IT technology',
    'site:indsponsors.nl Netherlands',
    'site:visa-hunt.com Netherlands tech',
    'site:arrowlancer.com Netherlands',
    'site:startup.jobs Netherlands',
    'site:hnhiring.com Amsterdam',
    'site:jobs.uprotterdam.com',
    'site:magnet.me "visa sponsorship" Netherlands tech',
    'site:workway.dev Netherlands',
    'site:vacaturebank.ai Netherlands',
    'site:eurotoptech.com Netherlands',
    'site:owliejobs.com Netherlands',
    'site:jobmetasearch.ai "visa sponsorship" Netherlands',
    'site:expatjobs.io Netherlands tech',
    'site:moveabroadjobs.com Netherlands',
    'site:jaabz.com Netherlands',

    # ── PM-specific boards ───────────────────────────────────────────────────
    'site:jobs.mindtheproduct.com Netherlands OR Amsterdam',
    'site:producthired.com Netherlands OR Amsterdam',
    'site:fintechcareers.com Amsterdam OR Netherlands',
    'site:inferencejobs.com Amsterdam "product manager"',
    'site:weloveproduct.co Netherlands',

    # ── Dutch job boards (English-language listings) ─────────────────────────
    'site:werkzoeken.nl "English" Netherlands developer "visa"',
    'site:jobbird.com Netherlands developer "visa sponsorship"',
    'site:intermediair.nl Netherlands IT "visa sponsorship"',
    'site:monsterboard.nl "software engineer" Netherlands "visa"',
    'site:nationalevacaturebank.nl developer Netherlands "English" "visa"',

    # ── Recruiter / agency job boards ────────────────────────────────────────
    'site:trinamics.nl Netherlands developer',
    'site:sourcegroup.com Netherlands developer "visa"',
    'site:wearedevelopers.com Netherlands',
    'site:hays.nl "software" Netherlands "visa sponsorship"',
    'site:roberthalfnl.nl developer Netherlands',
    'site:yacht.nl "developer" OR "engineer" Netherlands "visa"',
]


def niche_boards_source(config: dict) -> List[Job]:
    """
    Brave Search against niche NL boards not covered by Adzuna or jobspy.
    Covers: tech boards, PM-specific boards, Dutch job boards (English listings),
    and recruiter/agency sites.
    """
    api_key = os.environ.get("SERPER_API_KEY", "")
    if not api_key:
        logger.warning("SERPER_API_KEY not set — skipping niche_boards")
        return []

    jobs = []
    for query in _NICHE_BOARD_QUERIES:
        for item in _serper_search(query, api_key, count=10):
            url = item.get("url", "")
            if not url:
                continue
            jobs.append(Job(
                url=url,
                title=item.get("title", "")[:150],
                company="",
                location="Netherlands",
                description=item.get("description", ""),
                source="niche_boards",
            ))
        time.sleep(0.3)

    logger.info(f"Niche boards: {len(jobs)} raw jobs")
    return jobs


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
        return False
    loc_lower = location.lower()
    return any(kw in loc_lower for kw in NL_LOCATION_KEYWORDS)