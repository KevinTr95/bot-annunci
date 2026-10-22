"""Lettura del CV in PDF: estrae testo, skill e lingue."""
from __future__ import annotations

import io
import re
from typing import List, Set, Tuple

from pypdf import PdfReader

from .models import fold
from .skills import extract_skills

_LANG_PATTERNS = {
    "italiano": r"(italian|italiano)",
    "inglese": r"(english|inglese)",
    "tedesco": r"(german|tedesco|deutsch)",
    "francese": r"(french|francese|francais)",
    "spagnolo": r"(spanish|spagnolo|espanol)",
}


def parse_cv(pdf_bytes: bytes) -> Tuple[str, Set[str], List[str]]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    skills = extract_skills(text)
    t = fold(text)
    languages = [name for name, pat in _LANG_PATTERNS.items() if re.search(pat, t)]
    return text, skills, languages
