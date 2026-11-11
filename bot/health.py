"""Messaggio di ripristino: se dall'ultimo giro riuscito ci sono stati giri falliti (o i giri non
sono proprio partiti per un po'), il primo giro buono lo dice su Telegram, con orari e causa."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from html import escape
from typing import List, Optional
from zoneinfo import ZoneInfo

import requests

from .config import TIMEZONE
from .models import parse_dt

API = "https://api.github.com"
STATUS = "https://www.githubstatus.com/api/v2/incidents.json"
WORKFLOW = "bot.yml"
PAUSE_ALERT = 20 * 60          # senza giri falliti, avvisa solo dopo 20 minuti di silenzio
BAD = {"failure", "startup_failure", "timed_out", "cancelled"}
# Incidenti GitHub che possono fermare il bot (Copilot e simili non c'entrano)
_RELEVANT = ("actions", "git", "webhooks", "api", "multiple")


def _hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts, ZoneInfo(TIMEZONE)).strftime("%H:%M")


def _span(a: float, b: float) -> str:
    day = lambda t: datetime.fromtimestamp(t, ZoneInfo(TIMEZONE)).strftime("%d/%m")
    if day(a) != day(b):
        return f"dal {day(a)} alle {_hhmm(a)} al {day(b)} alle {_hhmm(b)}"
    return f"dalle {_hhmm(a)} alle {_hhmm(b)}" if _hhmm(a) != _hhmm(b) else f"alle {_hhmm(a)}"


def _gh(path: str, **params) -> dict:
    r = requests.get(f"{API}/repos/{os.environ['GITHUB_REPOSITORY']}/{path}", params=params, timeout=10,
                     headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                              "Accept": "application/vnd.github+json"})
    r.raise_for_status()
    return r.json()


def _failed_runs(since: float, until: Optional[float] = None) -> List[dict]:
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    created = f"{iso(since)}..{iso(until)}" if until else f">={iso(since)}"
    runs: List[dict] = []
    for page in range(1, 6):   # 100 giri a pagina: copre anche un fermo di un giorno e mezzo
        batch = _gh(f"actions/workflows/{WORKFLOW}/runs", created=created, per_page=100,
                    page=page).get("workflow_runs", [])
        runs += batch
        if len(batch) < 100:
            break
    return [r for r in runs if r.get("conclusion") in BAD]


def _failed_steps(runs: List[dict]) -> List[str]:
    """Nomi degli step falliti (es. "Salva stato"), guardando al massimo 3 giri."""
    names: List[str] = []
    for run in runs[:3]:
        try:
            for job in _gh(f"actions/runs/{run['id']}/jobs").get("jobs", []):
                names += [s["name"] for s in job.get("steps", []) if s.get("conclusion") == "failure"]
        except Exception:
            continue
    return list(dict.fromkeys(names))


def _incident(start: float, end: float) -> Optional[str]:
    """Nome di un incidente GitHub che si sovrappone alla finestra (con 15 minuti di margine)."""
    try:
        incidents = requests.get(STATUS, timeout=8).json().get("incidents", [])
    except Exception:
        return None
    for i in incidents:
        a, b = parse_dt(i.get("created_at")), parse_dt(i.get("resolved_at"))
        if not a:
            continue
        a, b = a.timestamp(), (b.timestamp() if b else time.time())
        if a <= end + 900 and b >= start - 900 and any(k in i.get("name", "").lower() for k in _RELEVANT):
            return i["name"]
    return None


def recovery_message(last_ok: Optional[float], now: Optional[float] = None) -> Optional[str]:
    """Messaggio da mandare se dall'ultimo giro riuscito (`last_ok`) qualcosa è andato storto.
    `now` serve solo per le prove su fermi passati."""
    if not last_ok or not os.environ.get("GH_TOKEN") or not os.environ.get("GITHUB_REPOSITORY"):
        return None
    test = now is not None
    now = now or time.time()
    try:
        failed = _failed_runs(last_ok + 1, now if test else None)
    except Exception:
        failed = []
    if failed:
        times = sorted(parse_dt(r["created_at"]).timestamp() for r in failed)
        start, end = times[0], times[-1]
        n = len(failed)
        what = f"{n} giro non è andato" if n == 1 else f"{n} giri non sono andati"
        incident = _incident(start, end)
        if incident:
            cause = f"guasto di GitHub: «{escape(incident)}»"
        elif all(r.get("conclusion") == "startup_failure" for r in failed):
            cause = "GitHub non ha avviato i giri"
        else:
            steps = _failed_steps(failed)
            cause = f"errore nello step «{escape(', '.join(steps))}»" if steps else "causa non indicata da GitHub"
        head = f"{_span(start, end).capitalize()} {what} a buon fine ({cause})."
    elif now - last_ok > PAUSE_ALERT:
        minutes = round((now - last_ok) / 60)
        incident = _incident(last_ok, now)
        cause = f"guasto di GitHub: «{escape(incident)}»" if incident else "non sono stati avviati"
        head = f"Per {minutes} minuti, {_span(last_ok, now)}, i giri non sono partiti ({cause})."
    else:
        return None
    return (f"✅ <b>Il bot ha ripreso a funzionare.</b>\n{head}\n"
            "Gli annunci usciti nel frattempo li recupero in questo giro.")
