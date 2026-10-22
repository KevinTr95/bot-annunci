"""Estrazione (best effort) della retribuzione annua dichiarata in un annuncio."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .models import fold

_NUM = r"(\d{2,3}(?:[.,'’ ]\d{3})?(?:[.,]\d{1,2})?\s?k?)"
_CUR = r"(€|eur|euro|chf|fr\.|sfr|ral)"
_SEP = r"\s*(?:-|–|—|a|to|bis|fino a|/)\s*"
_PATTERNS = [
    # "RAL 45.000 - 55.000 €", "CHF 100'000 – 120'000", "€50k-60k"
    re.compile(_CUR + r"\s*:?\s*" + _NUM + r"(?:" + _SEP + r"(?:€|eur|chf)?\s*" + _NUM + r")?"),
    # "45.000 - 55.000 €", "50k€", "100'000 CHF"
    re.compile(_NUM + r"(?:\s*" + _CUR + r")?" + _SEP + _NUM + r"\s*" + _CUR),
    re.compile(_NUM + r"\s*" + _CUR),
]


@dataclass
class Salary:
    low: float
    high: float
    currency: str  # "EUR" | "CHF"
    text: str

    def short(self) -> str:
        sym = "€" if self.currency == "EUR" else "CHF"
        lo, hi = round(self.low / 1000), round(self.high / 1000)
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


def parse_salary(text: str) -> Optional[Salary]:
    t = fold(text)
    for pat in _PATTERNS:
        for m in pat.finditer(t):
            groups = m.groups()
            nums = [_to_number(g) for g in groups if g and re.match(r"\d", g)]
            nums = [n for n in nums if n]
            curs = [g for g in groups if g and not re.match(r"\d", g)]
            if not nums:
                continue
            # Teniamo solo importi annui plausibili (scarta stipendi mensili e numeri casuali).
            if not all(15_000 <= n <= 400_000 for n in nums):
                continue
            cur = "CHF" if any(c in ("chf", "fr.", "sfr") for c in curs) else "EUR"
            return Salary(min(nums), max(nums), cur, m.group(0).strip())
    return None
