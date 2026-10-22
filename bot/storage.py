"""Profilo e stato su file JSON (su GitHub vivono nel branch `bot-state`).

Con STATE_KEY impostata i file sono cifrati (`*.json.enc`), perché il repository è pubblico.
Un file in chiaro trovato con la chiave impostata viene letto e, al primo salvataggio,
sostituito dalla versione cifrata."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from cryptography.fernet import Fernet

from .config import DATA_DIR, DEFAULT_PROFILE_PATH, PROFILE_PATH, STATE_KEY, STATE_PATH

SEEN_TTL_DAYS = 60


def _enc_path(path: Path) -> Path:
    return path.with_name(path.name + ".enc")


def _read(path: Path, default):
    enc = _enc_path(path)
    if STATE_KEY and enc.exists():
        # Una chiave sbagliata deve fermare il giro (InvalidToken), non azzerare lo stato.
        return json.loads(Fernet(STATE_KEY).decrypt(enc.read_bytes()))
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write(path: Path, data) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True).encode()
    target = path
    if STATE_KEY:
        raw = Fernet(STATE_KEY).encrypt(raw)
        target = _enc_path(path)
    tmp = target.with_suffix(".tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, target)
    if STATE_KEY and path.exists():
        path.unlink()   # niente copie in chiaro accanto a quella cifrata


def default_profile() -> dict:
    return json.loads(DEFAULT_PROFILE_PATH.read_text())


def load_profile() -> dict:
    profile = default_profile()
    profile.update(_read(PROFILE_PATH, {}))   # campi nuovi del default restano disponibili
    if isinstance(profile.get("skills"), dict):   # vecchio formato {skill: livello}
        profile["skills"] = sorted(profile["skills"])
    return profile


def save_profile(profile: dict) -> None:
    _write(PROFILE_PATH, profile)


def load_state() -> dict:
    state = {
        "seen": {},            # uid annuncio → epoch prima volta visto
        "fingerprints": {},    # azienda|titolo → epoch, per non notificare doppioni fra fonti
        "tg_offset": 0,
        "owner_chat_id": None,
        "sources": {},         # nome → {"fails": n, "last_ok": epoch, "alerted": bool, "last_error": str}
        "last_run": None,
        "last_linkedin_ok": None,
        "recent": [],          # ultime notifiche, per /ultimi
        "stats": {"runs": 0, "notified": 0, "durations": []},
    }
    state.update(_read(STATE_PATH, {}))
    return state


def save_state(state: dict) -> None:
    cutoff = time.time() - SEEN_TTL_DAYS * 86400
    for key in ("seen", "fingerprints"):
        state[key] = {k: v for k, v in state[key].items() if v >= cutoff}
    state["recent"] = state["recent"][-30:]
    state["stats"]["durations"] = state["stats"]["durations"][-50:]
    _write(STATE_PATH, state)
