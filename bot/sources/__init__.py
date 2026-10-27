"""Registro delle fonti: ogni fonte è un compito (nome, funzione) che restituisce annunci."""
from __future__ import annotations

from typing import Callable, List, Tuple

from ..models import Job
from . import ats, jobsch, linkedin

Task = Tuple[str, Callable[[], List[Job]]]


def build_tasks(profile: dict, linkedin_state: dict) -> List[Task]:
    enabled = profile.get("sources", {})
    tasks: List[Task] = []
    if enabled.get("linkedin", True):
        tasks.append(("linkedin", lambda: linkedin.fetch(profile, linkedin_state)))
    if enabled.get("jobs.ch", True):
        tasks.append(("jobs.ch", lambda: jobsch.fetch(profile)))
    if enabled.get("aziende", True):
        for c in profile.get("companies", []):
            tasks.append((f"azienda:{c['name']}", lambda c=c: ats.fetch_company(c)))
    return tasks


def fetch_detail(job: Job) -> None:
    if job.source == "linkedin":
        linkedin.fetch_detail(job)
    elif job.source == "jobs.ch":
        jobsch.fetch_detail(job)
    elif job.source in ats.DETAIL:
        ats.DETAIL[job.source](job)
    job.needs_detail = False
