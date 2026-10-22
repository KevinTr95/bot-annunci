"""jobs.ch: API JSON pubblica usata dal sito stesso."""
from __future__ import annotations

from typing import List

from .. import http
from ..models import Job, html_to_text, parse_dt
from ..zones import jobsch_locations

SEARCH = "https://www.jobs.ch/api/v1/public/search"
DETAIL = "https://www.jobs.ch/api/v1/public/search/job/{id}"


def _to_job(d: dict) -> Job:
    return Job(
        source="jobs.ch",
        source_id=d["job_id"],
        title=html_to_text(d.get("title", "")),
        company=d.get("company_name") or "",
        location=html_to_text(d.get("place", "")),
        url=d["_links"]["detail_en"]["href"],
        # La data della *prima* pubblicazione: gli annunci ripubblicati non sembrano nuovi.
        posted_at=parse_dt(d.get("initial_publication_date") or d.get("publication_date")),
        description=d.get("preview", ""),
        needs_detail=True,
    )


def fetch(profile: dict) -> List[Job]:
    seen, out = set(), []
    for loc in jobsch_locations(profile.get("zones", [])):
        for kw in profile.get("keywords", []):
            for page in (1, 2):   # l'API accetta al massimo 20 risultati per pagina
                params = {"query": kw, "rows": 20, "page": page, "sort": "date"}
                if loc:
                    params["location"] = loc
                data = http.get(SEARCH, params=params).json()
                for d in data.get("documents", []):
                    if d["job_id"] not in seen:
                        seen.add(d["job_id"])
                        out.append(_to_job(d))
                if data.get("num_pages", 1) <= page:
                    break
    return out


def fetch_detail(job: Job) -> None:
    d = http.get(DETAIL.format(id=job.source_id)).json()
    skills = ", ".join(s.get("name", "") for s in d.get("skills", []) if isinstance(s, dict))
    job.description = "\n".join(filter(None, [html_to_text(d.get("template_text") or d.get("template", "")),
                                              job.description, skills]))
