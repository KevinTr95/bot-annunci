"""LinkedIn: ricerca pubblica (senza login) tramite le pagine "guest".

Non è un'API ufficiale: LinkedIn può rallentare o bloccare le richieste, soprattutto
dagli IP dei datacenter. In quel caso il giro continua con le altre fonti.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import List

from bs4 import BeautifulSoup

from .. import http
from ..models import Job, html_to_text, parse_dt
from ..zones import linkedin_locations

SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"
MAX_PAGES = 3


def _text(node) -> str:
    return " ".join(node.get_text(" ").split()) if node else ""


def _parse_cards(html: str) -> List[Job]:
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for card in soup.select("div.base-card[data-entity-urn]"):
        job_id = card["data-entity-urn"].rsplit(":", 1)[-1]
        link = card.select_one("a.base-card__full-link")
        when = card.select_one("time")
        salary = _text(card.select_one(".job-search-card__salary-info"))
        jobs.append(Job(
            source="linkedin",
            source_id=job_id,
            title=_text(card.select_one(".base-search-card__title")),
            company=_text(card.select_one(".base-search-card__subtitle")),
            location=_text(card.select_one(".job-search-card__location")),
            url=(link["href"].split("?")[0] if link else f"https://www.linkedin.com/jobs/view/{job_id}"),
            posted_at=parse_dt(when.get("datetime")) if when else None,
            description=salary,
            needs_detail=True,
        ))
    return jobs


def _or_query(keywords: List[str]) -> str:
    """Tutte le parole in una sola ricerca: "data engineer" OR dbt OR ... (meno richieste = meno blocchi)."""
    return " OR ".join(f'"{k}"' if " " in k else k for k in keywords)


def fetch(profile: dict, window_hours: int) -> List[Job]:
    query = _or_query(profile.get("keywords", []))
    locations = linkedin_locations(profile.get("zones", []))
    seen, out = set(), []
    try:
        # La ricerca pubblica ignora il filtro "remoto" (f_WT): per il remoto si cerca su tutto
        # il paese e la modalità si ricava poi dalla descrizione.
        for loc in dict.fromkeys(loc for loc, _ in locations):
            for page in range(MAX_PAGES):
                params = {"keywords": query, "location": loc, "f_TPR": f"r{window_hours * 3600}",
                          "sortBy": "DD", "start": page * 10}
                cards = _parse_cards(http.get(SEARCH, params=params).text)
                for j in cards:
                    if j.source_id not in seen:
                        seen.add(j.source_id)
                        out.append(j)
                time.sleep(1.0)
                if len(cards) < 10:
                    break
    except http.RateLimited:
        if not out:
            raise
        # Bloccati a metà: teniamo quello che abbiamo, il resto al prossimo giro.
    return out


def fetch_detail(job: Job) -> None:
    soup = BeautifulSoup(http.get(DETAIL.format(id=job.source_id)).text, "html.parser")
    desc = soup.select_one(".show-more-less-html__markup")
    criteria = " · ".join(_text(li) for li in soup.select(".description__job-criteria-item"))
    job.description = "\n".join(filter(None, [job.description, html_to_text(str(desc)) if desc else "", criteria]))
    applicants = _text(soup.select_one(".num-applicants__caption"))
    if applicants:
        job.extra["applicants"] = applicants
    # La lista dà solo il giorno; il dettaglio dice "3 hours ago": più preciso.
    ago = _text(soup.select_one(".posted-time-ago__text")).lower()
    m = re.search(r"(\d+)\s+(minute|minut|hour|or[ae]|day|giorn|week|settiman)", ago)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        hours = n / 60 if unit.startswith("minut") else n if unit in ("hour", "ora", "ore") else \
            n * 24 if unit in ("day", "giorn") else n * 24 * 7
        job.posted_at = datetime.now(timezone.utc) - timedelta(hours=hours)
