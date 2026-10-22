"""Modalità di lavoro (remoto / ibrido / in sede) e lingua dell'annuncio, dedotte dal testo.

Le fonti quasi mai lo dicono in modo strutturato (la ricerca pubblica di LinkedIn ignora
perfino il suo filtro), quindi si cercano le formule tipiche in italiano, inglese,
tedesco e francese. Se non c'è nessun indizio la modalità resta ignota (None).
"""
from __future__ import annotations

import re
from typing import Optional

from .models import Job, fold

_FULL_REMOTE = re.compile(
    r"(?<![a-z])(full[- ]?remote|fully remote|100 ?% remote|100 ?% (da )?remoto|remote[- ]first|"
    r"completamente (da )?remoto|totalmente (da )?remoto|interamente (da )?remoto|full smart[- ]?working|"
    r"smart working (al )?100|remote position|position is remote|role is remote|work from anywhere|"
    r"remote only|anywhere in (italy|europe|switzerland)|100 ?% home ?office|vollstandig remote)(?![a-z])")
_HYBRID = re.compile(
    r"(?<![a-z])(hybrid(?! ?(cloud|infra\w*|environment|architect\w*|solution\w*|integration|app\w*|data))|"
    r"ibrid[oa]|smart[- ]?working|lavoro agile|"
    r"\d ?(days?|giorni|tage|jours?) (a week |alla settimana |a settimana |pro woche |par semaine )?"
    r"(from home|remote|remotely|in (the )?office|in sede|in ufficio|da remoto|da casa|di smart|in presenza|"
    r"home ?office|im buro|au bureau|en teletravail)|"
    r"home ?office|homeoffice|remote working|flexible working|flexible work|teletravail|telelavoro|"
    r"mobiles arbeiten|remote friendly|partially remote|parzialmente (da )?remoto)(?![a-z])")
_ONSITE = re.compile(
    r"(?<![a-z])(on[- ]?site|in sede|in presenza|100 ?% in office|full[- ]time in (the )?office|"
    r"5 days (a week )?in (the )?office|presenza in ufficio|vor ort|sur site)(?![a-z])")
_REMOTE_WORD = re.compile(r"(?<![a-z])(remote|remoto|da remoto|telelavoro|anywhere)(?![a-z])")

LABELS = {"remote": "🏠 full remote", "hybrid": "🏢🏠 ibrido", "onsite": "🏢 in sede"}


def work_mode(job: Job) -> Optional[str]:
    if job.extra.get("workplace") in ("remote", "hybrid", "onsite"):
        return job.extra["workplace"]
    head = fold(f"{job.title} {job.location}")
    if job.remote or _REMOTE_WORD.search(head):
        return "remote" if not _HYBRID.search(head) else "hybrid"
    text = fold(job.description)
    if _FULL_REMOTE.search(text):
        return "remote"
    if _HYBRID.search(text):
        return "hybrid"
    if _ONSITE.search(text):
        return "onsite"
    return None


_DE = re.compile(r"(?<![a-z])(und|der|die|das|wir|sie|mit|fur|bei|eine?n?|ist|auf|zu|unser|ihre?)(?![a-z])")
_FR = re.compile(r"(?<![a-z])(et|les|des|nous|vous|avec|pour|une|dans|est|votre|notre|sur)(?![a-z])")
_EN = re.compile(r"(?<![a-z])(and|the|with|for|our|you|we|are|is|of|to|in|your)(?![a-z])")
_IT = re.compile(r"(?<![a-z])(e|il|la|di|per|con|che|del|della|un|una|nel|siamo|sei|tuo)(?![a-z])")


def text_language(text: str) -> Optional[str]:
    """Lingua prevalente di una descrizione ('tedesco', 'francese', ...) o None se troppo corta."""
    t = fold(text)
    if len(t.split()) < 60:
        return None
    counts = {"tedesco": len(_DE.findall(t)), "francese": len(_FR.findall(t)),
              "inglese": len(_EN.findall(t)), "italiano": len(_IT.findall(t))}
    lang, n = max(counts.items(), key=lambda kv: kv[1])
    return lang if n >= 10 else None
