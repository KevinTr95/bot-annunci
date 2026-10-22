"""Filtri e punteggio di compatibilità (0–100) fra un annuncio e il profilo.

Composizione del punteggio:
  ruolo nel titolo   25  (15 se il titolo combacia solo in parte)
  skill              55  (copertura delle skill citate; una skill "parente" vale 0.4)
  zona               10  (5 se la località non è indicata)
  seniority          10  (penalità per junior, anni richiesti troppi, ruoli da direttore)
  lingue            -10  se l'annuncio chiede una lingua che non hai (-3 se solo gradita)

Filtri rigidi (scartano l'annuncio): azienda o parola nel titolo escluse, ruolo non pertinente,
fuori zona, modalità di lavoro non ammessa, RAL dichiarata sotto il minimo, nessuna parola
obbligatoria, % di skill possedute sotto `min_skill_match`, lingua obbligatoria che non parli
(se `languages_strict`), anni di esperienza richiesti oltre `years_experience + max_extra_years`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .models import Job, fold
from .salary import Salary, parse_salary
from .skills import canonical, extract_skills
from .workmode import text_language, work_mode
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
OPEN_SALARY_RATIO = 0.65

_LANGS = {
    "tedesco": r"(german|tedesco|deutsch)",
    "francese": r"(french|francese|francais)",
    "spagnolo": r"(spanish|spagnolo|espanol)",
}
# Anni di esperienza richiesti: il numero deve stare vicino a "experience/esperienza/...",
# così "fondata 20 anni fa" o "da oltre 30 anni sul mercato" non contano.
_EXP_WORD = r"(experience|esperienza|erfahrung|berufserfahrung|professional|working|work experience|in (a )?similar|nel ruolo|in ruoli)"
_YEARS = r"(\d{1,2})\s*\+?\s*(?:(?:-|–|to|a|bis)\s*\d{1,2}\s*\+?\s*)?(?:years?|anni|yrs|jahre|ans)"
_YEARS_AFTER = re.compile(_YEARS + r"(?:\s+\w+){0,6}?\s*(?:of\s+|di\s+)?" + _EXP_WORD)
_YEARS_BEFORE = re.compile(_EXP_WORD + r"[^.\n]{0,40}?(?:almeno|at least|minimum|minimo|min\.?|mindestens|oltre|over|more than)?\s*" + _YEARS)


def required_years(text_fold: str) -> Optional[int]:
    """Anni minimi richiesti (in un intervallo "3-5 anni" conta il 3); None se non indicati."""
    found = [int(m.group(1)) for m in _YEARS_AFTER.finditer(text_fold)]
    found += [int(m.group(m.lastindex)) for m in _YEARS_BEFORE.finditer(text_fold)]
    found = [y for y in found if 1 <= y <= 15]   # oltre 15 è quasi sempre la storia dell'azienda
    return max(found) if found else None


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
    mode: Optional[str] = None          # remote | hybrid | onsite | None
    skill_pct: Optional[int] = None     # % delle skill citate che possiedi (simili = mezza)


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
        # Con "a partire da X" il massimo è ignoto: scarta solo se X è molto sotto il minimo.
        if floor and (salary.low < OPEN_SALARY_RATIO * floor if salary.open_ended else salary.high < floor):
            return None, f"RAL sotto il minimo ({salary.short()})"

    warnings: List[str] = []

    # Modalità di lavoro
    mode = work_mode(job)
    modes = profile.get("work_modes") or []
    if modes and mode and mode not in modes:
        return None, f"modalità {mode}"
    if modes and not mode:
        if profile.get("work_mode_strict"):
            return None, "modalità di lavoro non indicata"
        warnings.append("modalità di lavoro non indicata")

    # Lingue: con languages_strict una lingua obbligatoria che non parli scarta l'annuncio
    have = {fold(l) for l in profile.get("languages", [])}
    strict = profile.get("languages_strict", False)
    written_in = text_language(job.description)
    if strict and written_in and written_in not in have:
        return None, f"annuncio scritto in {written_in}"
    s_lang = 0
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
            if strict:
                return None, f"richiesto il {name}"
            s_lang -= 10
            warnings.append(f"richiesto il {name}")
        break

    # Ruolo
    frac, role = role_match(job.title, profile.get("roles", []))
    s_role = 25 if frac >= 0.999 else 15

    # Zona (ricontrollata ora che la modalità è nota: "Italy" vale solo se full remote)
    ok, zone = location_match(job, profile.get("zones", []), mode)
    if ok is False:
        return None, "fuori zona"
    s_zone = 10 if ok else 5
    if ok is None:
        warnings.append("località non indicata")

    # Skill
    mine = {canonical(s) for s in profile.get("skills", [])}
    wanted = extract_skills(text, extra=mine)
    matched = sorted(s for s in wanted if s in mine)
    similar: List[Tuple[str, str]] = []
    missing: List[str] = []
    credit = float(len(matched))
    for s in sorted(wanted - set(matched)):
        kin = next((m for fam in FAMILIES if s in fam for m in fam if m in mine and m != s), None)
        if kin:
            similar.append((s, kin))
            credit += PARTIAL_CREDIT
        else:
            missing.append(s)
    skill_pct = None
    if wanted:
        skill_pct = int(round(100 * (len(matched) + 0.5 * len(similar)) / len(wanted)))
        if skill_pct < profile.get("min_skill_match", 0):
            return None, f"skill possedute {skill_pct}%"
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
    yoe = int(profile.get("years_experience", 0) or 0)
    years = required_years(tf)
    if years and yoe and years > yoe:
        if years > yoe + int(profile.get("max_extra_years", 2)):
            return None, f"chiede {years} anni di esperienza"
        s_sen -= 4 * (years - yoe)
        warnings.append(f"chiede {years} anni (ne hai {yoe})")

    score = int(round(max(0, min(100, s_role + s_zone + s_skill + s_sen + s_lang))))
    return Match(score, role, zone or "", matched, similar, missing, warnings, salary, mode, skill_pct), ""
