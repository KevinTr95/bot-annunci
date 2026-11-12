"""Estrazione (best effort) della retribuzione annua dichiarata in un annuncio."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .models import fold

# Importo intero: mai un pezzo di un numero più lungo (es. "202" dentro "2026").
_NUM = r"(?<![\d.,'’])(\d{2,3}(?:[.,'’ ]\d{3})(?:[.,]\d{1,2})?|\d{5,6}(?:[.,]\d{1,2})?|\d{2,3}(?:[.,]\d{1,2})?\s?k?)(?![\d])"
_CUR = r"(€|eur|euro|chf|fr\.|sfr|ral)"
_SEP = r"\s*(?:-|–|—|a|to|bis|fino a|/)\s*"
# "/yr", "/anno", "annui", "lordi", "p.a.": fra l'importo e il separatore del range
_UNIT = r"(?:\s*(?:/\s*(?:yr|year|anno|annum|a)(?![a-z])|annu[ie](?![a-z])|annual|lord[io](?![a-z])|gross|p\.\s?a\.|per year|all.anno|l.anno))*"
# Parole che annunciano la retribuzione anche senza valuta: "RAL compresa nella forbice 30-35K"
_KEY = (r"(?<![a-z])(?:r\.?a\.?l\.?|retribuzion[ei]|retributiv[aoie]|stipendio|salary|salario|salaire|"
        r"compenso|compensation|remunerazione|remuneration|gehalt|lohn)(?![a-z])")
# ...ma non "salary: 25 giorni di ferie" o "fino al 30% di bonus"
_NOT_MONEY = r"(?!\s*(?:%|giorn|days?\b|ann[io]\b|years?\b|ore\b|hours?\b|mes[ie]\b|months?\b|settiman|weeks?\b|dipendent|employ|person|ferie|mila\b|milion|million|mln|mld|miliard|billion|bn\b))"
_OPTCUR = r"\s*(€|eur|euro|chf|fr\.|sfr)?"
# Importi in altre valute o non annuali: si scartano
_FOREIGN = re.compile(r"\$|(?<![a-z])(usd|gbp|inr|aud|cad)(?![a-z])|£|₹")
_PER_TIME = re.compile(r"\s*(?:€|eur|euro|chf|\$)?\s*(?:gross|lord[io])?\s*(?:/|per\b|all.|al\b|a\b|an\b|pro\b)\s*"
                       r"(?:h|hr|hrs|hour|std|stunde|ora|ore|mese|month|mo|monat|day|giorno|tag|week|settimana)(?![a-z])")
_PATTERNS = [
    # "RAL 45.000 - 55.000 €", "CHF 100'000 – 120'000", "€50k-60k", "€30.000/yr - €35.000/anno"
    re.compile(_CUR + r"\s*:?\s*" + _NUM + r"(?:\s*(?:€|eur|euro|chf))?" + _UNIT + r"(?:" + _SEP
               + r"(?:€|eur|chf)?\s*" + _NUM + r")?"),
    # "45.000 - 55.000 €", "50k€", "100'000 CHF", "30.000,00 €/yr - 35.000,00 €/anno"
    re.compile(_NUM + r"(?:\s*" + _CUR + r")?" + _UNIT + _SEP + _NUM + r"\s*" + _CUR),
    re.compile(_NUM + r"\s*" + _CUR),
]
# Ultima possibilità: importo vicino a una parola come RAL o salary, anche senza valuta.
_KEYWORD = re.compile(_KEY + r"[^\d\n]{0,60}?" + _OPTCUR + r"\s*" + _NUM + _UNIT + _OPTCUR + r"(?:" + _SEP
                      + r"(?:max\.?\s*)?(?:€|eur|euro|chf)?\s*" + _NUM + r")?" + _UNIT + _OPTCUR + _NOT_MONEY)


_OPEN = re.compile(r"(a partire da|partire da|starting( from| at)?|from|minimum|minimo|almeno|at least|ab|des|da)\s*:?\s*$")


@dataclass
class Salary:
    low: float
    high: float
    currency: str  # "EUR" | "CHF"
    text: str
    open_ended: bool = False   # "a partire da 45.000": è il minimo della fascia, non il massimo

    def short(self) -> str:
        sym = "€" if self.currency == "EUR" else "CHF"
        lo, hi = round(self.low / 1000), round(self.high / 1000)
        if self.open_ended and lo == hi:
            return f"da {lo}k {sym}"
        return f"{lo}k {sym}" if lo == hi else f"{lo}–{hi}k {sym}"


def _to_number(raw: str) -> Optional[float]:
    s = raw.strip().replace(" ", "")
    k = s.endswith("k")
    s = s.rstrip("k")
    s = re.sub(r"[.,'’](\d{3})", r"\1", s)   # separatori delle migliaia
    s = s.replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    return v * 1000 if k else v


def parse_salary(text: str, default_currency: str = "EUR") -> Optional[Salary]:
    """Cerca la RAL nel testo. `default_currency` vale quando l'importo non ha valuta
    ("RAL 30-35K"): CHF per gli annunci svizzeri, EUR per gli altri."""
    t = fold(text)
    for keyword, pat in [(False, p) for p in _PATTERNS] + [(True, _KEYWORD)]:
        for m in pat.finditer(t):
            groups = m.groups()
            raw = [g for g in groups if g and re.match(r"\d", g)]
            nums = [_to_number(g) for g in raw]
            if not raw or not all(nums):
                continue
            curs = [g for g in groups if g and not re.match(r"\d", g)]
            near = t[max(0, m.start() - 25):m.end() + 25]
            explicit = any(c in ("€", "eur", "euro", "chf", "fr.", "sfr") for c in curs)
            # "$145,000", "15 €/h", "2.000 al mese": non è una RAL in euro/franchi
            if _FOREIGN.search(m.group(0)) or (not explicit and _FOREIGN.search(near)):
                continue
            if _PER_TIME.match(t, m.end()):
                continue
            # Vicino a "salary" un numero isolato senza k né valuta ("19 paesi") non è un importo
            if keyword and len(raw) == 1 and not explicit and not raw[0].strip().endswith("k") and nums[0] < 1000:
                continue
            # "30-35K": la k vale per tutti e due. Vicino a "RAL" anche "30-35" sono migliaia.
            if any(g.strip().endswith("k") for g in raw) or (keyword and all(n < 1000 for n in nums)):
                nums = [n * 1000 if n < 1000 else n for n in nums]
            # Teniamo solo importi annui plausibili (scarta stipendi mensili e numeri casuali).
            if not all(15_000 <= n <= 400_000 for n in nums):
                continue
            if any(c in ("chf", "fr.", "sfr") for c in curs) or re.search(r"(?<![a-z])(chf|sfr)(?![a-z])", near):
                cur = "CHF"
            elif any(c in ("€", "eur", "euro") for c in curs):
                cur = "EUR"
            else:
                cur = default_currency
            before = t[max(0, m.start() - 30):m.start()]
            open_ended = len(nums) == 1 and bool(_OPEN.search(before))
            return Salary(min(nums), max(nums), cur, m.group(0).strip(), open_ended)
    return None


def from_fields(low, high, currency: str, period: str = "year") -> Optional[Salary]:
    """RAL dai campi strutturati di una fonte (jobs.ch, Lever, Greenhouse).
    Solo annuale e in EUR/CHF: il resto non si confronta con le tue soglie."""
    cur = (currency or "").upper()
    if cur not in ("EUR", "CHF") or not str(period).lower().startswith(("year", "per-year", "annual")):
        return None
    vals = [float(v) for v in (low, high) if v not in (None, "", 0)]
    if not vals or not all(15_000 <= v <= 400_000 for v in vals):
        return None
    lo, hi = min(vals), max(vals)
    return Salary(lo, hi, cur, f"{lo:.0f}-{hi:.0f} {cur}")


def job_salary(job, default_currency: str = "EUR") -> Optional[Salary]:
    """La RAL di un annuncio: prima i campi strutturati della fonte, poi il testo (descrizione,
    riquadro retribuzione di LinkedIn, campi aggiuntivi)."""
    e = job.extra
    if e.get("sal_low") or e.get("sal_high"):
        s = from_fields(e.get("sal_low"), e.get("sal_high"), e.get("sal_cur", ""), e.get("sal_period", "year"))
        if s:
            return s
    return parse_salary(job.full_text, default_currency)
