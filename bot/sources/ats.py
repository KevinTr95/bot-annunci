"""Pagine carriere delle aziende, lette dalle API pubbliche dei loro ATS.

Supportati: Greenhouse, Lever, Workable, SmartRecruiters, Recruitee, Personio.
Ogni azienda nel profilo è {"name": ..., "ats": ..., "slug": ...}.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Callable, Dict, List, Optional, Tuple

import requests

from .. import http
from ..models import Job, fold, html_to_text, parse_dt


# ---------- Greenhouse
def _gh_list(slug: str, name: str) -> List[Job]:
    data = http.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs").json()
    return [Job(
        source="greenhouse", source_id=f"{slug}/{j['id']}", title=j["title"].strip(),
        company=name or j.get("company_name", slug), location=(j.get("location") or {}).get("name", ""),
        url=j["absolute_url"], posted_at=parse_dt(j.get("first_published") or j.get("updated_at")),
        needs_detail=True,
    ) for j in data.get("jobs", [])]


def _gh_detail(job: Job) -> None:
    slug, jid = job.source_id.split("/", 1)
    d = http.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{jid}").json()
    job.description = html_to_text(d.get("content", ""))


# ---------- Lever (account US o EU)
def _lever_list(slug: str, name: str) -> List[Job]:
    data = None
    for host in ("api.lever.co", "api.eu.lever.co"):
        try:
            data = http.get(f"https://{host}/v0/postings/{slug}?mode=json", timeout=25).json()
            if data:
                break
        except requests.HTTPError:
            continue
    if data is None:
        raise RuntimeError("Lever non risponde")
    out = []
    for j in data:
        cat = j.get("categories") or {}
        lists = "\n".join(f"{l.get('text', '')}\n{html_to_text(l.get('content', ''))}" for l in j.get("lists", []))
        out.append(Job(
            source="lever", source_id=f"{slug}/{j['id']}", title=j["text"].strip(), company=name or slug,
            location=" / ".join(cat.get("allLocations") or [cat.get("location", "")]),
            url=j["hostedUrl"], posted_at=parse_dt(j.get("createdAt")),
            description="\n".join([j.get("descriptionPlain", ""), lists, j.get("additionalPlain", "")]),
            remote=j.get("workplaceType") == "remote",
        ))
    return out


# ---------- Workable
def _workable_list(slug: str, name: str) -> List[Job]:
    data = http.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true").json()
    out = []
    for j in data.get("jobs", []):
        locs = j.get("locations") or [{"city": j.get("city"), "country": j.get("country")}]
        loc = " / ".join(", ".join(filter(None, [l.get("city"), l.get("country")])) for l in locs)
        out.append(Job(
            source="workable", source_id=f"{slug}/{j['shortcode']}", title=j["title"].strip(),
            company=name or data.get("name", slug), location=loc, url=j["url"],
            posted_at=parse_dt(j.get("published_on") or j.get("created_at")),
            description=html_to_text(j.get("description", "")), remote=bool(j.get("telecommuting")),
        ))
    return out


# ---------- SmartRecruiters
def _sr_list(slug: str, name: str) -> List[Job]:
    data = http.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100").json()
    out = []
    for j in data.get("content", []):
        loc = j.get("location") or {}
        out.append(Job(
            source="smartrecruiters", source_id=f"{slug}/{j['id']}", title=j["name"].strip(),
            company=name or (j.get("company") or {}).get("name", slug),
            location=loc.get("fullLocation") or ", ".join(filter(None, [loc.get("city"), loc.get("country")])),
            url=f"https://jobs.smartrecruiters.com/{slug}/{j['id']}", posted_at=parse_dt(j.get("releasedDate")),
            remote=bool(loc.get("remote")), needs_detail=True,
        ))
    return out


def _sr_detail(job: Job) -> None:
    slug, jid = job.source_id.split("/", 1)
    d = http.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{jid}").json()
    sections = (d.get("jobAd") or {}).get("sections") or {}
    job.description = "\n".join(html_to_text((s or {}).get("text", "")) for s in sections.values())


# ---------- Recruitee
def _recruitee_list(slug: str, name: str) -> List[Job]:
    data = http.get(f"https://{slug}.recruitee.com/api/offers/").json()
    return [Job(
        source="recruitee", source_id=f"{slug}/{j['id']}", title=j["title"].strip(),
        company=name or j.get("company_name", slug),
        location=j.get("location") or ", ".join(filter(None, [j.get("city"), j.get("country")])),
        url=j.get("careers_url", ""), posted_at=parse_dt(j.get("published_at") or j.get("created_at")),
        description=html_to_text(f"{j.get('description', '')}\n{j.get('requirements', '')}"),
        remote=bool(j.get("remote")),
    ) for j in data.get("offers", [])]


# ---------- Personio
def _personio_list(slug: str, name: str) -> List[Job]:
    root = ET.fromstring(http.get(f"https://{slug}.jobs.personio.de/xml").content)
    out = []
    for p in root.findall("position"):
        desc = "\n".join(html_to_text(v.text or "") for v in p.iter("value"))
        jid = p.findtext("id", "")
        out.append(Job(
            source="personio", source_id=f"{slug}/{jid}", title=(p.findtext("name") or "").strip(),
            company=name or slug, location=p.findtext("office", ""),
            url=f"https://{slug}.jobs.personio.de/job/{jid}", posted_at=parse_dt(p.findtext("createdAt")),
            description=desc,
        ))
    return out


LISTERS: Dict[str, Callable[[str, str], List[Job]]] = {
    "greenhouse": _gh_list,
    "lever": _lever_list,
    "workable": _workable_list,
    "smartrecruiters": _sr_list,
    "recruitee": _recruitee_list,
    "personio": _personio_list,
}
DETAIL: Dict[str, Callable[[Job], None]] = {
    "greenhouse": _gh_detail,
    "smartrecruiters": _sr_detail,
}

_URL_PATTERNS = [
    (r"greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)", "greenhouse"),
    (r"jobs(?:\.eu)?\.lever\.co/([\w-]+)", "lever"),
    (r"apply\.workable\.com/([\w-]+)", "workable"),
    (r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)", "smartrecruiters"),
    (r"([\w-]+)\.recruitee\.com", "recruitee"),
    (r"([\w-]+)\.jobs\.personio\.(?:de|com)", "personio"),
]


def fetch_company(company: dict) -> List[Job]:
    return LISTERS[company["ats"]](company["slug"], company.get("name", ""))


def detect(query: str) -> Optional[Tuple[str, str, int]]:
    """Da un nome o un link trova (ats, slug, numero di annunci) provando i vari ATS."""
    q = query.strip()
    for pat, ats in _URL_PATTERNS:
        m = re.search(pat, q)
        if m:
            try:
                return ats, m.group(1), len(LISTERS[ats](m.group(1), ""))
            except Exception:
                return None
    base = fold(q)
    slugs = list(dict.fromkeys([re.sub(r"[^a-z0-9]", "", base), re.sub(r"[^a-z0-9]+", "-", base).strip("-")]))
    best = None
    for slug in slugs:
        for ats, lister in LISTERS.items():
            try:
                n = len(lister(slug, ""))
            except Exception:
                continue
            if n and (best is None or n > best[2]):
                best = (ats, slug, n)
    return best
