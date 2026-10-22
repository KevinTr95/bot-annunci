"""Filtri e punteggio di compatibilità (0–100) fra un annuncio e il profilo.

Composizione del punteggio:
  ruolo nel titolo   25  (15 se il titolo combacia solo in parte)
  skill              55  (copertura delle skill citate, pesata sul tuo livello)
  zona               10  (5 se la località non è indicata)
  seniority          10  (penalità per junior, anni richiesti troppi, ruoli da direttore)
  lingue            -10  se l'annuncio chiede una lingua che non hai (-3 se solo gradita)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .models import Job, fold
from .salary import Salary, parse_salary
from .skills import canonical, extract_skills
from .zones import location_match

# Skill "parenti": se hai una skill della famiglia, le altre valgono in parte.
# L'ordine conta: come termine di paragone si sceglie la prima che possiedi.
FAMILIES = [
    ["BigQuery", "Snowflake", "Redshift", "Databricks", "Synapse", "ClickHouse", "Vertica", "Teradata"],
    ["Talend", "Informatica", "SSIS", "Azure Data Factory", "NiFi", "AWS Glue", "Airbyte", "Fivetran"],
    ["Qlik", "Power BI", "Tableau", "Looker"],
    ["GCP", "AWS", "Azure"],
    ["Airflow", "Dagster", "Prefect", "Cloud Composer"],
    ["PostgreSQL", "MySQL", "Oracle", "SQL Server", "Vertica", "Teradata"],
    ["Kafka", "Pub/Sub", "Flink", "Streaming"],
]
PARTIAL_CREDIT = 0.4

_LANGS = {
    "tedesco": r"(german|tedesco|deutsch)",
    "francese": r"(french|francese|francais)",
    "spagnolo": r"(spanish|spagnolo|espanol)",
}
_NICE = r"(plus|nice to have|preferred|advantage|bonus|gradit|vantaggio|apprezzat|preferenziale|von vorteil)"
_STOP = {"di", "dei", "del", "the", "of", "and", "e", "&", "-", "/"}


@dataclass
class Match:
    score: int
    role: str
    zone: str
    matched: List[str] = field(default_factory=list)
    similar: List[Tuple[str, str]] = field(default_factory=list)   # (skill annuncio, tua skill simile)
    missing: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    salary: Optional[Salary] = None


def _tokens(s: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9+#]+", fold(s)) if t not in _STOP]


def role_match(title: str, roles: List[str]) -> Tuple[float, str]:
    """Quanto il titolo corrisponde al ruolo più vicino (1.0 = tutte le parole presenti)."""
    t_fold = fold(title)
    t_tok = _tokens(title)
    best, best_role = 0.0, ""
    for role in roles:
        r_tok = _tokens(role)
        if not r_tok:
            continue
        if fold(role) in t_fold:
            return 1.0, role
        # "engineer" vale anche per "engineering", ma "data" non vale per "database".
        hit = sum(1 for rt in r_tok
                  if any(tt == rt or (len(rt) >= 5 and tt.startswith(rt) and len(tt) - len(rt) <= 3)
                         for tt in t_tok))
        frac = hit / len(r_tok)
        if frac > best:
            best, best_role = frac, role
    return best, best_role


_ROLE_NOUNS = re.compile(r"(?<![a-z])(engineer|engineering|developer|architect|specialist|consultant|"
                         r"sviluppatore|ingegnere|programmer|programmatore)(?![a-z])")


def _role_ok(frac: float, role: str, title: str) -> bool:
    """Titolo pienamente coerente, oppure quasi (2 parole su 3) ma comunque un ruolo tecnico."""
    if frac >= 0.999:
        return True
    return frac >= 0.66 and len(_tokens(role)) >= 3 and bool(_ROLE_NOUNS.search(fold(title)))


def _contains_any(text_fold: str, words: List[str]) -> Optional[str]:
    for w in words:
        if re.search(r"(?<![a-z0-9])" + re.escape(fold(w).strip()) + r"(?![a-z0-9])", text_fold):
            return w
    return None


def prefilter(job: Job, profile: dict) -> Optional[str]:
    """Filtro veloce su titolo, azienda e località, prima di scaricare la descrizione.
    Restituisce il motivo dello scarto, oppure None se l'annuncio va valutato."""
    if _contains_any(fold(job.company), profile.get("exclude_companies", [])):
        return "azienda esclusa"
    if _contains_any(fold(job.title), profile.get("exclude_words", [])):
        return "parola esclusa nel titolo"
    frac, role = role_match(job.title, profile.get("roles", []))
    if not _role_ok(frac, role, job.title):
        return "ruolo non pertinente"
    ok, _ = location_match(job, profile.get("zones", []))
    if ok is False:
        return "fuori zona"
    return None


def evaluate(job: Job, profile: dict) -> Tuple[Optional[Match], str]:
    """Punteggio completo. (None, motivo) se un filtro rigido scarta l'annuncio."""
    reason = prefilter(job, profile)
    if reason:
        return None, reason
    text = job.full_text
    tf = fold(text)

    required = profile.get("required_words", [])
    if required and not _contains_any(tf, required):
        return None, "nessuna parola obbligatoria"

    salary = parse_salary(text)
    if salary:
        floor = profile.get("ral_min_chf") if salary.currency == "CHF" else profile.get("ral_min_eur")
        if floor and salary.high < floor:
            return None, f"RAL sotto il minimo ({salary.short()})"

    warnings: List[str] = []

    # Ruolo
    frac, role = role_match(job.title, profile.get("roles", []))
    s_role = 25 if frac >= 0.999 else 15

    # Zona
    ok, zone = location_match(job, profile.get("zones", []))
    s_zone = 10 if ok else 5
    if ok is None:
        warnings.append("località non indicata")

    # Skill
    mine: Dict[str, int] = {canonical(k): int(v) for k, v in profile.get("skills", {}).items()}
    wanted = extract_skills(text, extra=mine.keys())
    matched = sorted((s for s in wanted if s in mine), key=lambda s: (-mine[s], s))
    similar: List[Tuple[str, str]] = []
    missing: List[str] = []
    credit = sum(mine[s] / 5 for s in matched)
    for s in sorted(wanted - set(matched)):
        kin = next((m for fam in FAMILIES if s in fam for m in fam if m in mine and m != s), None)
        if kin:
            similar.append((s, kin))
            credit += PARTIAL_CREDIT * mine[kin] / 5
        else:
            missing.append(s)
    if wanted:
        # Con poche skill citate il giudizio è incerto: il denominatore minimo è 5.
        coverage = credit / max(len(wanted), 5)
        s_skill = 55 * min(1.0, coverage * 1.15)
    else:
        s_skill = 22
        warnings.append("nessuna skill riconosciuta nella descrizione")

    # Seniority
    s_sen = 10
    title_f = fold(job.title)
    if re.search(r"(?<![a-z])(junior|jr)(?![a-z])", title_f):
        s_sen -= 10
        warnings.append("ruolo junior")
    if re.search(r"(?<![a-z])(head of|director|direttore|vp|vice president|chief)(?![a-z])", title_f):
        s_sen -= 6
        warnings.append("ruolo dirigenziale")
    years = [int(y) for y in re.findall(r"(\d{1,2})\s*\+?\s*(?:years|anni|yrs|jahre|ans)", tf) if int(y) < 25]
    yoe = int(profile.get("years_experience", 0) or 0)
    if years and yoe and max(years) > yoe + 2:
        s_sen -= 8
        warnings.append(f"chiede {max(years)}+ anni di esperienza")

    # Lingue: penalità piena se richiesta, lieve se solo "gradita"
    s_lang = 0
    have = {fold(l) for l in profile.get("languages", [])}
    for name, pat in _LANGS.items():
        if name in have:
            continue
        hits = [sent for sent in re.split(r"[.\n;•]", tf) if re.search(r"(?<![a-z])" + pat + r"(?![a-z])", sent)]
        if not hits:
            continue
        if all(re.search(_NICE, sent) for sent in hits):
            s_lang -= 3
            warnings.append(f"{name} gradito")
        else:
            s_lang -= 10
            warnings.append(f"richiesto il {name}")
        break

    score = int(round(max(0, min(100, s_role + s_zone + s_skill + s_sen + s_lang))))
    return Match(score, role, zone or "", matched, similar, missing, warnings, salary), ""
