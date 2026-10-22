"""Zone geografiche: come riconoscerle nella località di un annuncio e come cercarle sui portali."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .models import Job, fold

_LOMBARDIA = [
    "lombardia", "lombardy", "milano", "milan", "monza", "brianza", "bergamo", "brescia", "como",
    "varese", "lecco", "pavia", "lodi", "cremona", "mantova", "sondrio", "sesto san giovanni",
    "segrate", "assago", "rho", "cinisello", "paderno dugnano", "busto arsizio", "gallarate",
    "legnano", "saronno", "vimercate", "agrate", "san donato milanese", "rozzano", "cologno monzese",
    "basiglio", "bollate", "arese", "cernusco", "pero", "settimo milanese", "corsico", "opera",
    "peschiera borromeo", "san giuliano milanese", "melegnano", "ispra", "lainate", "garbagnate",
    "novate milanese", "cormano", "bresso", "pioltello", "carugate", "trezzano", "buccinasco",
]
_TICINO = [
    "ticino", "tessin", "lugano", "chiasso", "mendrisio", "bellinzona", "locarno", "stabio", "manno",
    "balerna", "novazzano", "agno", "bioggio", "biasca", "giubiasco", "coldrerio", "morbio", "vacallo",
    "castel san pietro", "ligornetto", "gordola", "losone", "ascona", "cadempino", "lamone",
    "massagno", "paradiso", "savosa", "viganello", "pregassona", "taverne", "bedano", "grancia",
    "melide", "riva san vitale", "capriasca", "sorengo", "muzzano", "rivera", "monteceneri",
    "contone", "sant'antonino", "s. antonino", "cadenazzo", "arbedo", "genestrerio", "rancate",
]
_SVIZZERA = ["switzerland", "svizzera", "schweiz", "suisse", "zurich", "zurigo", "zürich", "zug",
             "basel", "bern", "geneva", "ginevra", "lausanne", "losanna", "winterthur", "luzern"] + _TICINO

# chiave → (nome mostrato, parole riconosciute, località per LinkedIn, località per jobs.ch)
PRESETS: Dict[str, Tuple[str, List[str], List[str], List[str]]] = {
    "lombardia": ("Lombardia", _LOMBARDIA, ["Lombardy, Italy"], []),
    "milano": ("Milano", _LOMBARDIA, ["Milan, Lombardy, Italy"], []),
    "ticino": ("Canton Ticino", _TICINO, ["Canton of Ticino, Switzerland"], ["Ticino"]),
    "svizzera": ("Svizzera", _SVIZZERA, ["Switzerland"], [""]),
    "italia": ("Italia", ["italy", "italia"], ["Italy"], []),
}
_PRESET_ALIASES = {"milan": "milano", "lombardy": "lombardia", "tessin": "ticino",
                   "canton ticino": "ticino", "switzerland": "svizzera", "italy": "italia",
                   "lugano": "ticino"}

REMOTE_KEYS = {"remote", "remoto", "full remote", "smart working", "da remoto"}
_REMOTE_RE = re.compile(r"(?<![a-z])(remote|remoto|da remoto|full[- ]remote|telelavoro|home ?office|"
                        r"work from home|wfh|anywhere)(?![a-z])")
_REMOTE_OK_REGION = re.compile(r"(italy|italia|switzerland|svizzera|schweiz|suisse|europe|europa|emea|"
                               r"\beu\b|cet|cest|ital)")


def is_remote_zone(zone: str) -> bool:
    return fold(zone).strip() in REMOTE_KEYS


def preset_key(zone: str) -> Optional[str]:
    f = fold(zone).strip()
    f = _PRESET_ALIASES.get(f, f)
    return f if f in PRESETS else None


def zone_words(zone: str) -> List[str]:
    key = preset_key(zone)
    return PRESETS[key][1] if key else [fold(zone).strip()]


def linkedin_locations(zones: List[str]) -> List[Tuple[str, bool]]:
    """Coppie (località, solo_remoto) da interrogare su LinkedIn."""
    out: List[Tuple[str, bool]] = []
    for z in zones:
        if is_remote_zone(z):
            out += [("Italy", True), ("Switzerland", True)]
            continue
        key = preset_key(z)
        out += [(loc, False) for loc in (PRESETS[key][2] if key else [z])]
    # Milano è già dentro Lombardia: evitiamo query doppie.
    if ("Lombardy, Italy", False) in out and ("Milan, Lombardy, Italy", False) in out:
        out.remove(("Milan, Lombardy, Italy", False))
    return list(dict.fromkeys(out))


def jobsch_locations(zones: List[str]) -> List[str]:
    out: List[str] = []
    for z in zones:
        key = preset_key(z)
        if key:
            out += PRESETS[key][3]
    return list(dict.fromkeys(out))


def detect_remote(job: Job) -> bool:
    if job.remote:
        return True
    return bool(_REMOTE_RE.search(fold(f"{job.title} {job.location}")))


def location_match(job: Job, zones: List[str]) -> Tuple[Optional[bool], str]:
    """(True, zona) se la località rientra nelle zone, (False, '') se no, (None, '') se ignota."""
    loc = fold(job.location)
    if detect_remote(job) and any(is_remote_zone(z) for z in zones):
        rest = _REMOTE_RE.sub(" ", loc)
        rest = re.sub(r"[^a-z]+", " ", rest).strip()
        if not rest or _REMOTE_OK_REGION.search(loc):
            return True, "Remote"
    if not loc.strip():
        return None, ""
    for z in zones:
        if is_remote_zone(z):
            continue
        for w in zone_words(z):
            if re.search(r"(?<![a-z])" + re.escape(fold(w)) + r"(?![a-z])", loc):
                key = preset_key(z)
                return True, PRESETS[key][0] if key else z
    return False, ""
