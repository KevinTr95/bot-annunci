"""Se un annuncio non indica la RAL, la cerca nello stesso annuncio sul sito carriere dell'azienda.

LinkedIn nasconde il link esterno dietro il login: si risale quindi dal nome dell'azienda al suo
ATS e si confrontano titolo e località, per non prendere la RAL di un'altra posizione."""
from __future__ import annotations

import re
import time
from typing import Dict

from .models import Job, fold
from .salary import job_salary
from .sources import ats
from .zones import is_swiss

FOUND_TTL = 30 * 86400
MISSING_TTL = 7 * 86400
_COUNTRY = {"italia", "italy", "svizzera", "switzerland", "schweiz", "suisse", "lombardia", "lombardy",
            "remote", "remoto", "provincia", "citta", "metropolitana"}


def _title(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", re.sub(r"\([^)]*\)", " ", fold(text))).strip()


def _same_place(job: Job, posting: Job) -> bool:
    if posting.remote or posting.extra.get("workplace") == "remote":
        return True
    words = [w for w in re.findall(r"[a-z]{4,}", fold(job.location)) if w not in _COUNTRY]
    return not words or any(w[:5] in fold(posting.location) for w in words)


def find_on_company_site(job: Job, cache: Dict[str, dict], deadline: float) -> bool:
    """True se ha trovato la RAL e l'ha registrata nell'annuncio.
    TimeoutError se il tempo finisce prima di aver potuto controllare."""
    key = fold(job.company).strip()
    if not key or job.source in ats.LISTERS:
        return False
    now = time.time()
    entry = cache.get(key)
    if not entry or now - entry["ts"] > (FOUND_TTL if entry.get("ats") else MISSING_TTL):
        found = ats.detect(job.company, deadline)
        entry = {"ats": found[0], "slug": found[1], "ts": int(now)} if found else {"ats": None, "ts": int(now)}
        cache[key] = entry
    if not entry["ats"]:
        return False
    if time.time() > deadline:
        raise TimeoutError
    title = _title(job.title)
    currency = "CHF" if is_swiss(job) else "EUR"
    for posting in ats.fetch_company({"name": job.company, "ats": entry["ats"], "slug": entry["slug"]}):
        if _title(posting.title) != title or not _same_place(job, posting):
            continue
        if posting.needs_detail and posting.source in ats.DETAIL:
            ats.DETAIL[posting.source](posting)
        salary = job_salary(posting, currency)
        if salary:
            kind = "open" if salary.open_ended else "cap" if salary.capped else ""
            job.extra.update(sal_low=salary.low, sal_high=salary.high, sal_cur=salary.currency,
                             sal_period="year", sal_kind=kind)
            return True
    return False
