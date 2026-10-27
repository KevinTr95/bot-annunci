"""LinkedIn: ricerca pubblica (senza login) tramite le pagine "guest".

Non è un'API ufficiale: LinkedIn può rallentare o bloccare le richieste, soprattutto
dagli IP dei datacenter. In quel caso il giro continua con le altre fonti.
"""
from __future__ import annotations

import math
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
PAUSE = 1.5          # secondi fra una richiesta e l'altra
RETRY_WAIT = 6       # attesa prima di riprovare dopo un 429
TIME_LIMIT = 22      # oltre, le zone mancanti passano al giro dopo (resta tempo per i dettagli)


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


def _get_with_retry(params: dict, ls: dict) -> str:
    """Una richiesta di ricerca; se LinkedIn risponde 429 aspetta e riprova una volta."""
    for attempt in range(2):
        try:
            return http.get(SEARCH, params=params).text
        except http.RateLimited:
            ls.setdefault("blocked", []).append(int(time.time()))
            if attempt:
                raise
            time.sleep(RETRY_WAIT)
    raise AssertionError("irraggiungibile")


def fetch(profile: dict, ls: dict) -> List[Job]:
    """Ricerca su tutte le zone. `ls` è lo stato LinkedIn del bot (state["linkedin"]):
    - loc_ok: zona → ultima ricerca completata, da cui si calcola la finestra di quella zona;
    - blocked: istanti dei 429 ricevuti (per /stato).

    Le zone si cercano partendo da quella aggiornata meno di recente: se LinkedIn ci blocca a
    metà giro, al giro dopo si riparte proprio da quelle rimaste indietro, e con una finestra
    abbastanza ampia da coprire il tempo perso."""
    query = _or_query(profile.get("keywords", []))
    now = time.time()
    loc_ok = ls.setdefault("loc_ok", {})
    # La ricerca pubblica ignora il filtro "remoto" (f_WT): per il remoto si cerca su tutto
    # il paese e la modalità si ricava poi dalla descrizione.
    locations = list(dict.fromkeys(loc for loc, _ in linkedin_locations(profile.get("zones", []))))
    locations.sort(key=lambda loc: loc_ok.get(loc, 0))
    ls["blocked"] = [t for t in ls.get("blocked", []) if now - t < 3600]

    seen, out, done = set(), [], 0
    try:
        for loc in locations:
            if time.time() - now > TIME_LIMIT:
                break
            last = loc_ok.get(loc)
            hours = 24 if not last else min(24, max(1, math.ceil((now - last) / 3600 + 0.5)))
            for page in range(MAX_PAGES):
                params = {"keywords": query, "location": loc, "f_TPR": f"r{hours * 3600}",
                          "sortBy": "DD", "start": page * 10}
                cards = _parse_cards(_get_with_retry(params, ls))
                for j in cards:
                    if j.source_id not in seen:
                        seen.add(j.source_id)
                        out.append(j)
                time.sleep(PAUSE)
                if len(cards) < 10:   # pagina non piena: non ce ne sono altre
                    break
            loc_ok[loc] = int(now)
            done += 1
    except http.RateLimited:
        if not done and not out:
            raise
        # Bloccati a metà: teniamo quello che abbiamo, le zone mancanti passano davanti al giro dopo.
    ls["last_partial"] = done < len(locations)
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
