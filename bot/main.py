"""Un giro del bot: comandi Telegram → raccolta annunci → filtri → punteggio → notifiche.

Uso:
  python -m bot.main              giro normale (come su GitHub Actions)
  python -m bot.main --dry-run    stampa le notifiche invece di mandarle, non salva lo stato
  python -m bot.main --detect X   prova a trovare la pagina carriere dell'azienda X
"""
from __future__ import annotations

import argparse
import math
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
from html import escape
from typing import Dict, List, Tuple
from zoneinfo import ZoneInfo

from . import config, http
from .commands import handle_updates
from .health import recovery_message
from .matcher import Match, evaluate, prefilter
from .models import Job, parse_dt
from .notify import job_message
from .sources import ats, build_tasks, fetch_detail
from .storage import effective_profile, load_profile, load_state, save_profile, save_state
from .telegram import Telegram
from .zones import linkedin_locations

MAX_DETAIL_TRIES = 24  # ~2 ore di giri ogni 5 minuti
ALERT_AFTER_FAILS = 6   # ~2 ore di errori di fila prima di avvisarti


def _clean(msg: str) -> str:
    """Toglie da un messaggio d'errore token e parametri di ricerca: i log del repo sono pubblici."""
    if config.TELEGRAM_TOKEN:
        msg = msg.replace(config.TELEGRAM_TOKEN, "***")
    return re.sub(r"\?[^\s'\")]*", "", msg)


def _tries(d) -> int:
    """Tentativi già fatti per un annuncio rimandato (vecchio formato: solo il numero)."""
    return d.get("n", 0) if isinstance(d, dict) else int(d or 0)


def _job_to_dict(job: Job) -> dict:
    return {"source": job.source, "source_id": job.source_id, "title": job.title, "company": job.company,
            "location": job.location, "url": job.url, "remote": job.remote,
            "posted_at": job.posted_at.isoformat() if job.posted_at else None,
            "extra": {k: v for k, v in job.extra.items() if isinstance(v, (str, int, float, bool))}}


def _job_from_dict(d: dict) -> Job:
    return Job(d["source"], d["source_id"], d["title"], d["company"], d.get("location", ""), d.get("url", ""),
               parse_dt(d.get("posted_at")), "", d.get("remote", False), needs_detail=True, extra=d.get("extra", {}))


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {_clean(msg)}", flush=True)


def _in_quiet_hours(profile: dict) -> bool:
    qh = profile.get("quiet_hours")
    if not qh:
        return False
    h = datetime.now(ZoneInfo(config.TIMEZONE)).hour
    start, end = qh
    return start <= h < end if start < end else (h >= start or h < end)


def _collect(tasks, state: dict) -> Tuple[List[Job], List[str], set, List[str]]:
    """Esegue le fonti in parallelo e aggiorna la loro salute. Restituisce (annunci, messaggi di
    allarme/ripristino, fonti alla prima raccolta riuscita, siti carriere guasti da ~2 ore)."""
    jobs: List[Job] = []
    alerts: List[str] = []
    broken: List[str] = []
    first_ok = set()
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(fn): name for name, fn in tasks}
        for fut, name in futures.items():
            h = state["sources"].setdefault(name, {"fails": 0, "last_ok": None, "alerted": False})
            try:
                found = fut.result(timeout=max(5, config.RUN_BUDGET_SECONDS))
            except Exception as e:
                h["fails"] += 1
                h["last_error"] = _clean(f"{type(e).__name__}: {e}")[:200]
                _log(f"{name}: ERRORE {h['last_error']}")
                if h["fails"] >= ALERT_AFTER_FAILS and not h["alerted"]:
                    h["alerted"] = True
                    if name.startswith("azienda:"):
                        broken.append(name)   # prima di avvisare si prova a ritrovarla (_repair_company)
                        continue
                    alerts.append(f"⚠️ La fonte <b>{escape(name)}</b> non risponde da {h['fails']} giri: "
                                  f"{escape(h['last_error'][:120])}")
                continue
            if h.get("alerted"):
                alerts.append(f"✅ La fonte <b>{escape(name)}</b> funziona di nuovo.")
            if not h.get("last_ok"):
                first_ok.add(name)
            h.update(fails=0, alerted=False, last_ok=int(time.time()))
            _log(f"{name}: {len(found)} annunci")
            for j in found:
                j.extra["task"] = name
            jobs.extend(found)
    return jobs, alerts, first_ok, broken


def _linkedin_details(jobs: List[Job], deadline: float) -> List[Job]:
    """LinkedIn risponde 429 alle raffiche: un dettaglio alla volta, con una pausa."""
    done, strikes = [], 0
    for job in jobs:
        if time.time() > deadline - 2:
            break
        try:
            fetch_detail(job)
            done.append(job)
            strikes = 0
        except http.RateLimited:
            strikes += 1
            if strikes >= 2:
                break
            time.sleep(2)
        except Exception as e:
            _log(f"dettaglio {job.uid}: {type(e).__name__}")
        time.sleep(0.8)
    return done


def _fetch_details(jobs: List[Job], deadline: float) -> List[Job]:
    """Scarica le descrizioni mancanti entro la scadenza. Restituisce gli annunci pronti;
    quelli rimasti indietro (tempo finito o fonte che ci rallenta) passano al giro dopo."""
    ready = [j for j in jobs if not j.needs_detail]
    li = [j for j in jobs if j.needs_detail and j.source == "linkedin"]
    other = [j for j in jobs if j.needs_detail and j.source != "linkedin"]
    if not li and not other:
        return ready
    ex = ThreadPoolExecutor(max_workers=5)
    li_future = ex.submit(_linkedin_details, li, deadline)
    futures = {ex.submit(fetch_detail, j): j for j in other}
    done, _ = wait(list(futures) + [li_future], timeout=max(1.0, deadline - time.time() + 3))
    ex.shutdown(wait=False, cancel_futures=True)
    if li_future in done:
        ready += li_future.result()
    for fut, job in futures.items():
        if fut not in done or isinstance(fut.exception(), http.RateLimited):
            continue
        if fut.exception():
            _log(f"dettaglio {job.uid}: {type(fut.exception()).__name__}")
            continue
        ready.append(job)
    return ready


def _repair_company(profile: dict, task: str, state: dict) -> Tuple[str, bool]:
    """Un sito carriere che non risponde da ~2 ore spesso ha solo cambiato ATS (es. da Greenhouse
    ad Ashby): lo si cerca di nuovo per nome e, se lo si trova altrove, si aggiorna il profilo.
    Restituisce (messaggio per Telegram, profilo cambiato)."""
    name = task.split(":", 1)[1]
    company = next((c for c in profile.get("companies", []) if c.get("name") == name), None)
    err = state["sources"].get(task, {}).get("last_error", "")
    found = None
    if company:
        try:
            found = ats.detect(name)
        except Exception:
            found = None
    if company and found and (found[0], found[1]) != (company["ats"], company["slug"]):
        old = company["ats"]
        company.update(ats=found[0], slug=found[1])
        state["sources"].pop(task, None)   # riparte come fonte nuova: niente raffica di annunci vecchi
        return (f"🔄 <b>{escape(name)}</b> ha cambiato sito carriere ({old} → {found[0]}, "
                f"{found[2]} annunci aperti): l'ho aggiornato, la seguo da lì."), True
    return (f"⚠️ Il sito carriere di <b>{escape(name)}</b> non risponde da {ALERT_AFTER_FAILS} giri "
            f"({escape(err[:100])}) e non lo ritrovo altrove. Se ha cambiato pagina: "
            f"<code>/aziende rm {escape(name)}</code> e poi <code>/aziende add</code> col link nuovo."), False


def run(dry_run: bool = False) -> None:
    t0 = time.time()
    profile, state = load_profile(), load_state()
    tg = Telegram(config.TELEGRAM_TOKEN) if config.TELEGRAM_TOKEN else None

    if tg and not dry_run:
        try:
            if handle_updates(tg, profile, state):
                save_profile(profile)
                _log("profilo aggiornato da Telegram")
        except Exception as e:
            _log(f"comandi Telegram: {type(e).__name__}: {e}")
    chat_id = config.TELEGRAM_CHAT_ID or state.get("owner_chat_id")
    if not dry_run and (not tg or not chat_id):
        _log("Telegram non configurato: imposta TELEGRAM_BOT_TOKEN e scrivi /start al bot.")
        save_state(state)
        return
    if not dry_run:
        # Dopo giri falliti (o una lunga pausa) un messaggio dice che il bot ha ripreso.
        try:
            msg = recovery_message(state.get("last_run"))
            if msg:
                tg.send(chat_id, msg, silent=_in_quiet_hours(profile))
                _log("inviato messaggio di ripristino")
        except Exception as e:
            _log(f"controllo ripristino: {type(e).__name__}")
    saved_profile = profile
    profile = effective_profile(profile)   # da qui in poi valgono anche le modifiche temporanee
    if profile.get("paused"):
        _log("in pausa")
        state["last_run"] = int(t0)
        save_state(state)
        return

    # LinkedIn gestisce da sé zone e finestre di ricerca (vedi sources/linkedin.py). Al primo giro
    # con questa logica le zone partono dall'ultimo giro riuscito, non da 24 ore fa.
    ls = state.setdefault("linkedin", {})
    if "loc_ok" not in ls and state.get("last_linkedin_ok"):
        ls["loc_ok"] = {loc: state["last_linkedin_ok"] for loc, _ in linkedin_locations(profile.get("zones", []))}
    jobs, alerts, first_ok, broken = _collect(build_tasks(profile, ls), state)
    for task in broken:
        msg, changed = _repair_company(saved_profile, task, state)
        alerts.append(msg)
        if changed and not dry_run:
            save_profile(saved_profile)

    now = time.time()
    max_age = profile.get("max_age_hours", 48) * 3600
    seen, fps = state["seen"], state["fingerprints"]
    candidates: Dict[str, Job] = {}
    skipped: Dict[str, int] = {}
    for job in jobs:
        if job.uid in seen or job.uid in candidates:
            continue
        if job.posted_at and now - job.posted_at.timestamp() > max_age:
            reason = "vecchio"
        elif not job.posted_at and job.extra.get("task") in first_ok:
            reason = "prima raccolta senza data"
        elif job.fingerprint in fps:
            reason = "già visto su un'altra fonte"
        else:
            reason = prefilter(job, profile)
        if reason:
            seen[job.uid] = int(now)
            skipped[reason] = skipped.get(reason, 0) + 1
        else:
            candidates[job.uid] = job
    _log(f"raccolti {len(jobs)}, candidati {len(candidates)}, scartati {sum(skipped.values())}")
    if config.VERBOSE:
        _log(f"motivi: {skipped}")

    # Gli annunci rimandati nei giri precedenti tornano in coda anche se la ricerca di oggi
    # non li restituisce più (la finestra di LinkedIn è breve), e passano per primi.
    deferred = state.get("deferred", {})
    for uid, d in deferred.items():
        if isinstance(d, dict) and uid not in seen and uid not in candidates:
            candidates[uid] = _job_from_dict(d["job"])
    order = sorted(candidates.values(), key=lambda j: -_tries(deferred.get(j.uid)))
    ready = _fetch_details(order, t0 + config.RUN_BUDGET_SECONDS)

    # Senza descrizione non si possono applicare RAL, skill e lingue: si riprova per ~2 ore,
    # poi si rinuncia (meglio perdere un annuncio che mandarne uno non filtrato).
    ready_ids = {j.uid for j in ready}
    new_deferred = {}
    for uid, job in candidates.items():
        if uid in ready_ids:
            continue
        n = _tries(deferred.get(uid)) + 1
        if n >= MAX_DETAIL_TRIES:
            seen[uid] = int(now)
            _log(f"descrizione non disponibile dopo {n} tentativi: annuncio lasciato perdere")
        else:
            new_deferred[uid] = {"n": n, "job": _job_to_dict(job)}
    state["deferred"] = new_deferred
    if new_deferred:
        _log(f"{len(new_deferred)} annunci rimandati al prossimo giro")

    matches: List[Tuple[Job, Match]] = []
    for job in ready:
        m, reason = evaluate(job, profile)
        if m and m.score >= profile.get("min_score", 60):
            matches.append((job, m))
        else:
            seen[job.uid] = int(now)
            fps[job.fingerprint] = int(now)
            if config.VERBOSE:
                _log(f"scartato ({reason or f'punteggio {m.score}'}): {job.title} – {job.company}")
    matches.sort(key=lambda jm: -jm[1].score)
    # Stesso annuncio da due fonti (o ripubblicato due volte) nello stesso giro: ne teniamo uno.
    unique: Dict[str, Tuple[Job, Match]] = {}
    for job, m in matches:
        if job.fingerprint in unique:
            seen[job.uid] = int(now)
        else:
            unique[job.fingerprint] = (job, m)
    matches = list(unique.values())

    silent = _in_quiet_hours(profile)
    sent = 0
    overflow: List[Tuple[Job, Match]] = []
    for job, m in matches:
        if sent >= config.MAX_NOTIFICATIONS_PER_RUN:
            overflow.append((job, m))
            continue
        text = job_message(job, m)
        if dry_run:
            print("\n" + text + f"\n🔗 {job.url}\n")
        else:
            try:
                tg.send(chat_id, text, button=("🔗 Apri annuncio", job.url), silent=silent)
            except Exception as e:
                _log(f"invio fallito, riprovo al prossimo giro: {e}")
                continue
        sent += 1
        seen[job.uid] = int(now)
        fps[job.fingerprint] = int(now)
        state["recent"].append({"title": job.title, "company": job.company, "url": job.url,
                                "score": m.score, "ts": int(now)})

    if overflow:
        rows = [f"• <a href=\"{escape(j.url)}\">{escape(j.title)}</a> – {escape(j.company)} ({m.score})"
                for j, m in overflow]
        alerts.append(f"➕ Altre {len(overflow)} offerte sopra soglia:\n" + "\n".join(rows))
        for j, _ in overflow:
            seen[j.uid] = int(now)
            fps[j.fingerprint] = int(now)
    for a in alerts:
        if dry_run:
            print(a)
        else:
            try:
                tg.send(chat_id, a, silent=silent)
            except Exception as e:
                _log(f"invio avviso fallito: {e}")

    state["last_run"] = int(t0)
    state["stats"]["runs"] += 1
    state["stats"]["notified"] += sent
    state["stats"]["durations"].append(round(time.time() - t0, 1))
    _log(f"notificati {sent} in {time.time() - t0:.1f}s")
    if not dry_run:
        save_state(state)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--detect")
    args = ap.parse_args()
    if args.detect:
        print(ats.detect(args.detect) or "nessuna pagina carriere trovata")
        return
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
