from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def fold(text: str) -> str:
    """Minuscolo e senza accenti, per confronti robusti."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower()


_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r"</?(p|div|br|li|ul|ol|h\d|tr)[^>]*>", re.I)


def html_to_text(raw: str) -> str:
    if not raw:
        return ""
    raw = html.unescape(raw)  # alcuni ATS (Greenhouse) mandano HTML già escapato
    raw = _BLOCK_RE.sub("\n", raw)
    text = html.unescape(_TAG_RE.sub(" ", raw))
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def parse_dt(value) -> Optional[datetime]:
    """ISO 8601, 'YYYY-MM-DD' o epoch (s/ms) → datetime UTC."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            ts = float(value)
            if ts > 1e11:
                ts /= 1000
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        s = str(value).strip().replace("Z", "+00:00")
        if len(s) == 10:
            s += "T00:00:00+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, OSError):
        return None


@dataclass
class Job:
    source: str          # es. "linkedin", "jobs.ch", "greenhouse"
    source_id: str
    title: str
    company: str
    location: str = ""
    url: str = ""
    posted_at: Optional[datetime] = None
    description: str = ""
    remote: bool = False
    needs_detail: bool = False   # descrizione da scaricare solo se l'annuncio passa i primi filtri
    extra: dict = field(default_factory=dict)

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.source_id}"

    @property
    def fingerprint(self) -> str:
        """Stesso annuncio pubblicato su fonti diverse → stessa impronta."""
        def norm(s: str) -> str:
            s = fold(s)
            s = re.sub(r"\((m/f|f/m|m/w|m/f/d|w/m/d|h/f|f/h|all genders)[^)]*\)", "", s)
            s = re.sub(r"\b(s\.?p\.?a|s\.?r\.?l|ag|sa|gmbh|ltd|inc|spa|srl)\b\.?", "", s)
            return re.sub(r"[^a-z0-9]+", " ", s).strip()
        return f"{norm(self.company)}|{norm(self.title)}"

    @property
    def full_text(self) -> str:
        return f"{self.title}\n{self.location}\n{self.description}"
