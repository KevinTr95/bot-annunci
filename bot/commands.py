"""Comandi Telegram per vedere e modificare il profilo."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from html import escape
from typing import Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from .config import TELEGRAM_CHAT_ID, TIMEZONE
from .cv import parse_cv
from .notify import age_text
from .skills import SKILLS, canonical
from .sources import ats
from .storage import default_profile
from .telegram import Telegram

# comando → (campo del profilo, descrizione)
LIST_FIELDS: Dict[str, Tuple[str, str]] = {
    "ruoli": ("roles", "Ruoli che cerchi (confrontati col titolo)"),
    "keyword": ("keywords", "Parole cercate su LinkedIn e jobs.ch"),
    "zone": ("zones", "Zone (Lombardia, Milano, Ticino, Svizzera, Italia, Remote o una città)"),
    "escludi": ("exclude_words", "Parole che, se nel titolo, scartano l'annuncio"),
    "aziende_escluse": ("exclude_companies", "Aziende da ignorare"),
    "obbligatorie": ("required_words", "L'annuncio deve contenerne almeno una (vuoto = nessun vincolo)"),
    "lingue": ("languages", "Lingue che parli"),
}

HELP = """<b>Comandi</b>
/profilo – riepilogo del profilo
📎 <i>invia il CV in PDF</i> – aggiorna le skill dal CV

<b>Liste</b> (senza argomenti mostrano la lista; più valori separati da virgola)
/skill add dbt:4, spark · /skill rm qlik – livello 1-5, default 3
/ruoli add data architect · /ruoli rm bi developer
/keyword add data engineer · /keyword rm dbt
/zone add Torino · /zone rm Remote
/escludi add junior · /escludi rm phd
/aziende_escluse add acme
/obbligatorie add bigquery, gcp
/lingue add tedesco

<b>Siti carriere delle aziende</b>
/aziende – elenco · /aziende add satispay · /aziende add https://jobs.lever.co/xyz · /aziende rm Nome

<b>Valori</b>
/ral 45000 · /ral off – RAL minima in € (scarta solo annunci con RAL dichiarata più bassa)
/ral_chf 90000 · /ral_chf off – lo stesso per gli annunci in CHF
/soglia 55 – punteggio minimo per ricevere la notifica
/eta 48 – ignora annunci pubblicati da più di N ore
/esperienza 6 – anni di esperienza
/silenzio 23 7 · /silenzio off – notifiche senza suono di notte
/fonti linkedin off · /fonti jobs.ch on · /fonti aziende on

<b>Bot</b>
/stato – salute delle fonti e ultimo giro
/ultimi – ultime offerte notificate
/pausa · /riprendi

ℹ️ Il bot legge i comandi a ogni giro (ogni 5–15 minuti): la risposta può arrivare con un po' di ritardo."""

BOT_COMMANDS = [
    ("profilo", "Riepilogo del profilo"), ("skill", "Skill: add/rm"), ("zone", "Zone: add/rm"),
    ("ruoli", "Ruoli: add/rm"), ("keyword", "Parole di ricerca: add/rm"), ("aziende", "Siti carriere: add/rm"),
    ("ral", "RAL minima in €"), ("soglia", "Punteggio minimo"), ("stato", "Salute delle fonti"),
    ("ultimi", "Ultime offerte notificate"), ("pausa", "Sospendi le notifiche"),
    ("riprendi", "Riattiva le notifiche"), ("aiuto", "Tutti i comandi"),
]


def _split(arg: str) -> List[str]:
    return [v.strip() for v in arg.split(",") if v.strip()]


def _parse_action(arg: str) -> Tuple[str, str]:
    parts = arg.split(None, 1)
    if not parts:
        return "", ""
    verb = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""
    if verb in ("add", "aggiungi", "+"):
        return "add", rest
    if verb in ("rm", "remove", "togli", "rimuovi", "del", "-"):
        return "rm", rest
    return "", arg


def profile_summary(p: dict) -> str:
    skills = sorted(p["skills"].items(), key=lambda kv: (-int(kv[1]), kv[0]))
    sk = ", ".join(f"{escape(k)} {'●' * int(v)}" for k, v in skills) or "—"
    qh = p.get("quiet_hours")
    lines = [
        "<b>👤 Il tuo profilo</b>",
        f"<b>Ruoli:</b> {escape(', '.join(p['roles']))}",
        f"<b>Parole di ricerca:</b> {escape(', '.join(p['keywords']))}",
        f"<b>Zone:</b> {escape(', '.join(p['zones']))}",
        f"<b>Skill:</b> {sk}",
        f"<b>Lingue:</b> {escape(', '.join(p['languages']))} · <b>Esperienza:</b> {p['years_experience']} anni",
        f"<b>RAL minima:</b> {p['ral_min_eur'] or '—'} € · {p['ral_min_chf'] or '—'} CHF",
        f"<b>Esclusi nel titolo:</b> {escape(', '.join(p['exclude_words'])) or '—'}",
        f"<b>Aziende escluse:</b> {escape(', '.join(p['exclude_companies'])) or '—'}",
        f"<b>Parole obbligatorie:</b> {escape(', '.join(p['required_words'])) or '—'}",
        f"<b>Soglia:</b> {p['min_score']}/100 · <b>Età massima:</b> {p['max_age_hours']} h",
        f"<b>Silenzio:</b> {f'{qh[0]}:00–{qh[1]}:00' if qh else 'off'}",
        f"<b>Fonti:</b> " + ", ".join(f"{k} {'✅' if v else '⛔'}" for k, v in p["sources"].items()),
        f"<b>Siti carriere seguiti:</b> {len(p['companies'])} (/aziende)",
    ]
    if p.get("paused"):
        lines.insert(1, "⏸ <b>In pausa</b> – /riprendi per riattivare")
    return "\n".join(lines)


def _handle_list(p: dict, cmd: str, arg: str) -> str:
    field, label = LIST_FIELDS[cmd]
    action, rest = _parse_action(arg)
    values = _split(rest)
    if action == "add":
        for v in values:
            if v.lower() not in (x.lower() for x in p[field]):
                p[field].append(v)
    elif action == "rm":
        low = {v.lower() for v in values}
        p[field] = [x for x in p[field] if x.lower() not in low]
    elif arg:
        return f"Uso: /{cmd} add valore1, valore2 · /{cmd} rm valore"
    items = "\n".join(f"• {escape(x)}" for x in p[field]) or "—"
    return f"<b>{label}</b>\n{items}"


def _handle_skill(p: dict, arg: str) -> str:
    action, rest = _parse_action(arg)
    skills: Dict[str, int] = p["skills"]
    if action == "add":
        for v in _split(rest):
            name, _, lvl = v.partition(":")
            level = int(lvl) if lvl.strip().isdigit() else 3
            skills[canonical(name)] = max(1, min(5, level))
    elif action == "rm":
        for v in _split(rest):
            skills.pop(canonical(v), None)
    elif arg:
        return "Uso: /skill add dbt:4, spark · /skill rm qlik"
    rows = sorted(skills.items(), key=lambda kv: (-int(kv[1]), kv[0]))
    body = "\n".join(f"{'●' * int(v)}{'○' * (5 - int(v))} {escape(k)}"
                     + ("" if k in SKILLS else " <i>(cercata letteralmente)</i>") for k, v in rows)
    return f"<b>Le tue skill</b> (livello 1-5)\n{body}"


def _handle_companies(p: dict, arg: str) -> str:
    action, rest = _parse_action(arg)
    if action == "add":
        if not rest:
            return "Uso: /aziende add satispay (oppure il link alla pagina carriere)"
        found = ats.detect(rest)
        if not found:
            return (f"Non trovo una pagina carriere compatibile per «{escape(rest)}». "
                    "Prova col link della pagina lavora-con-noi (Greenhouse, Lever, Workable, "
                    "SmartRecruiters, Recruitee o Personio).")
        kind, slug, n = found
        name = rest[:1].upper() + rest[1:] if not rest.startswith("http") else slug.replace("-", " ").title()
        p["companies"] = [c for c in p["companies"] if c["slug"] != slug] + [
            {"name": name, "ats": kind, "slug": slug}]
        return f"✅ Aggiunta <b>{escape(name)}</b> ({kind}, {n} annunci aperti ora)."
    if action == "rm":
        low = {v.lower() for v in _split(rest)}
        before = len(p["companies"])
        p["companies"] = [c for c in p["companies"] if c["name"].lower() not in low and c["slug"] not in low]
        return f"Rimosse {before - len(p['companies'])} aziende."
    rows = "\n".join(f"• {escape(c['name'])} <i>({c['ats']})</i>" for c in p["companies"]) or "—"
    return f"<b>Siti carriere seguiti</b>\n{rows}"


def _num(arg: str) -> Optional[int]:
    """'50000', '50.000', '50k' → 50000."""
    digits = "".join(ch for ch in arg if ch.isdigit())
    if not digits:
        return None
    return int(digits) * (1000 if arg.strip().lower().endswith("k") else 1)


def _handle_value(p: dict, cmd: str, arg: str) -> Optional[str]:
    a = arg.strip().lower()
    if cmd in ("ral", "ral_chf"):
        field = "ral_min_eur" if cmd == "ral" else "ral_min_chf"
        p[field] = None if a in ("off", "0", "no") else _num(a)
        return f"RAL minima {'€' if cmd == 'ral' else 'CHF'}: {p[field] or 'nessuna'}"
    if cmd == "soglia" and _num(a) is not None:
        p["min_score"] = max(0, min(100, _num(a)))
        return f"Soglia impostata a {p['min_score']}/100"
    if cmd == "eta" and _num(a):
        p["max_age_hours"] = _num(a)
        return f"Ignoro gli annunci più vecchi di {p['max_age_hours']} ore"
    if cmd == "esperienza" and _num(a) is not None:
        p["years_experience"] = _num(a)
        return f"Esperienza: {p['years_experience']} anni"
    if cmd == "silenzio":
        if a == "off":
            p["quiet_hours"] = None
            return "Notifiche sempre con suono"
        parts = [int(x) for x in a.split() if x.isdigit()]
        if len(parts) == 2:
            p["quiet_hours"] = [parts[0] % 24, parts[1] % 24]
            return f"Notifiche silenziose dalle {parts[0]}:00 alle {parts[1]}:00"
    if cmd == "fonti":
        parts = a.split()
        if len(parts) == 2 and parts[0] in p["sources"] and parts[1] in ("on", "off"):
            p["sources"][parts[0]] = parts[1] == "on"
        return "<b>Fonti</b>\n" + "\n".join(f"{'✅' if v else '⛔'} {k}" for k, v in p["sources"].items())
    return None


def _status(p: dict, state: dict) -> str:
    tz = ZoneInfo(TIMEZONE)
    last = state.get("last_run")
    durs = state["stats"].get("durations", [])
    lines = ["<b>📡 Stato</b>",
             f"Ultimo giro: {datetime.fromtimestamp(last, tz).strftime('%d/%m %H:%M') if last else 'mai'}",
             f"Giri totali: {state['stats']['runs']} · notifiche inviate: {state['stats']['notified']}",
             f"Durata media giro: {sum(durs) / len(durs):.0f}s" if durs else "",
             f"Annunci in memoria: {len(state['seen'])}",
             "⏸ In pausa" if p.get("paused") else ""]
    bad = []
    for name, h in sorted(state["sources"].items()):
        if h.get("fails"):
            bad.append(f"⚠️ {escape(name)}: {h['fails']} errori di fila ({escape(h.get('last_error', '')[:80])})")
    ok = sum(1 for h in state["sources"].values() if not h.get("fails"))
    lines.append(f"Fonti ok: {ok}")
    lines += bad
    return "\n".join(l for l in lines if l)


def _recent(state: dict) -> str:
    if not state["recent"]:
        return "Nessuna offerta notificata finora."
    rows = []
    for r in reversed(state["recent"][-10:]):
        rows.append(f"• <a href=\"{escape(r['url'])}\">{escape(r['title'])}</a> – {escape(r['company'])} "
                    f"({r['score']}/100, {age_text(datetime.fromtimestamp(r['ts'], timezone.utc))})")
    return "<b>Ultime offerte</b>\n" + "\n".join(rows)


def _handle_cv(p: dict, tg: Telegram, doc: dict) -> str:
    if not (doc.get("mime_type") == "application/pdf" or doc.get("file_name", "").lower().endswith(".pdf")):
        return "Mandami il CV in formato PDF."
    _, skills, languages = parse_cv(tg.download(doc["file_id"]))
    added = sorted(s for s in skills if s not in p["skills"])
    for s in added:
        p["skills"][s] = 3
    for lang in languages:
        if lang not in p["languages"]:
            p["languages"].append(lang)
    p["cv_updated_at"] = int(time.time())
    msg = [f"📄 CV letto: trovate {len(skills)} skill."]
    if added:
        msg.append("Nuove (livello 3): " + escape(", ".join(added)))
    msg.append("Regola i livelli con /skill add nome:livello (1-5), togli quelle sbagliate con /skill rm.")
    msg.append("Quando hai finito controlla tutto con /profilo.")
    return "\n".join(msg)


def handle_updates(tg: Telegram, profile: dict, state: dict) -> bool:
    """Elabora i messaggi arrivati dall'ultimo giro. True se il profilo è cambiato."""
    changed = False
    for upd in tg.get_updates(state["tg_offset"]):
        state["tg_offset"] = upd["update_id"] + 1
        msg = upd.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        if not chat_id:
            continue
        owner = TELEGRAM_CHAT_ID or state.get("owner_chat_id")
        text = (msg.get("text") or "").strip()
        if not owner and text.startswith("/start"):
            state["owner_chat_id"] = owner = chat_id
        if str(chat_id) != str(owner):
            tg.send(chat_id, "Questo è un bot privato.")
            continue

        before = repr(profile)
        try:
            reply = dispatch(tg, profile, state, text, msg)
        except Exception as e:  # un comando sbagliato non deve fermare il giro
            reply = f"⚠️ Errore nel comando: {escape(str(e))[:300]}"
        if reply:
            tg.send(chat_id, reply)
        changed |= repr(profile) != before
    return changed


def dispatch(tg: Telegram, p: dict, state: dict, text: str, msg: dict) -> Optional[str]:
    if msg.get("document"):
        return _handle_cv(p, tg, msg["document"])
    if not text.startswith("/"):
        return "Non ho capito. /aiuto per l'elenco dei comandi."
    head, _, arg = text.partition(" ")
    cmd = head[1:].split("@")[0].lower()
    arg = arg.strip()

    if cmd == "start":
        try:
            tg._call("setMyCommands", commands=[{"command": c, "description": d} for c, d in BOT_COMMANDS])
        except RuntimeError:
            pass
        return ("👋 Ciao! Ti avviso quando esce un annuncio nuovo adatto a te.\n\n"
                "Guarda il profilo di partenza con /profilo.\n"
                "Puoi anche mandarmi il CV in PDF per aggiornare le skill, e poi rifinire tutto "
                "con i comandi (/aiuto).")
    if cmd in ("aiuto", "help"):
        return HELP
    if cmd == "profilo":
        return profile_summary(p)
    if cmd == "skill":
        return _handle_skill(p, arg)
    if cmd in LIST_FIELDS:
        return _handle_list(p, cmd, arg)
    if cmd == "aziende":
        return _handle_companies(p, arg)
    if cmd == "pausa":
        p["paused"] = True
        return "⏸ Notifiche in pausa. /riprendi per riattivarle."
    if cmd == "riprendi":
        p["paused"] = False
        return "▶️ Notifiche riattivate."
    if cmd == "stato":
        return _status(p, state)
    if cmd == "ultimi":
        return _recent(state)
    if cmd == "reset_profilo":
        p.clear()
        p.update(default_profile())
        return "Profilo riportato ai valori iniziali."
    value_reply = _handle_value(p, cmd, arg)
    if value_reply:
        return value_reply
    return "Comando non riconosciuto. /aiuto per l'elenco."
