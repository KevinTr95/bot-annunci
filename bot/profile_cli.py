"""Modifica del profilo da file, senza passare dal bot.

  python -m bot.profile_cli scarica   → scrive profilo.json (in chiaro, escluso da git)
  python -m bot.profile_cli carica    → lo valida, mette in pausa il workflow, aspetta che
                                        finisca il giro in corso, pubblica il profilo cifrato
                                        nel branch bot-state e riattiva il workflow

Servono: STATE_KEY nel file .env e la CLI `gh` autenticata sul repository.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .config import ROOT, STATE_KEY

BRANCH = "bot-state"
WORKFLOW = "bot.yml"
PROFILE_FILE = ROOT / "profilo.json"
BASE_FILE = ROOT / ".profilo.base.json"   # copia scaricata: serve a capire se nel frattempo è cambiato


def _run(*cmd: str, **kw) -> str:
    return subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True, **kw).stdout


def _fetch_state(dest: Path) -> None:
    """Estrae l'ultimo contenuto del branch bot-state in dest."""
    _run("git", "fetch", "-q", "origin", BRANCH)
    archive = subprocess.run(["git", "archive", "FETCH_HEAD"], cwd=ROOT, check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive, check=True)


def _load_profile_from(data_dir: Path) -> dict:
    os.environ["BOT_DATA_DIR"] = str(data_dir)
    # storage legge i percorsi da config all'import: lo ricarichiamo puntando alla cartella giusta
    import importlib
    from . import config, storage
    importlib.reload(config)
    importlib.reload(storage)
    return storage.load_profile()


def _save_profile_to(data_dir: Path, profile: dict) -> None:
    from . import storage
    assert storage.PROFILE_PATH.parent == data_dir
    storage.save_profile(profile)


def _validate(p: dict) -> list:
    errors = []
    lists = ["roles", "keywords", "zones", "languages", "exclude_words", "exclude_companies", "required_words"]
    for key in lists:
        if not isinstance(p.get(key), list) or not all(isinstance(x, str) for x in p[key]):
            errors.append(f"«{key}» deve essere una lista di testi, es. [\"a\", \"b\"]")
    if not isinstance(p.get("skills"), dict) or not all(
            isinstance(v, int) and 1 <= v <= 5 for v in p["skills"].values()):
        errors.append("«skills» deve essere {\"SQL\": 5, \"dbt\": 4, ...} con livelli interi da 1 a 5")
    for key in ("ral_min_eur", "ral_min_chf"):
        if p.get(key) is not None and not isinstance(p[key], int):
            errors.append(f"«{key}» deve essere un numero intero (es. 50000) oppure null")
    if not isinstance(p.get("min_score"), int) or not 0 <= p["min_score"] <= 100:
        errors.append("«min_score» deve essere un intero fra 0 e 100")
    if not isinstance(p.get("max_age_hours"), int) or p["max_age_hours"] <= 0:
        errors.append("«max_age_hours» deve essere un intero positivo")
    qh = p.get("quiet_hours")
    if qh is not None and not (isinstance(qh, list) and len(qh) == 2 and all(isinstance(h, int) for h in qh)):
        errors.append("«quiet_hours» deve essere [23, 7] oppure null")
    for c in p.get("companies", []):
        if not {"name", "ats", "slug"} <= set(c):
            errors.append(f"azienda incompleta: {c} (servono name, ats, slug)")
    if not p.get("roles"):
        errors.append("«roles» non può essere vuota")
    return errors


def scarica() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _fetch_state(Path(tmp))
        profile = _load_profile_from(Path(tmp))
    text = json.dumps(profile, ensure_ascii=False, indent=2)
    PROFILE_FILE.write_text(text + "\n")
    BASE_FILE.write_text(text)
    print(f"✅ Profilo scaricato in {PROFILE_FILE.name}. Modificalo e poi lancia: "
          f"python -m bot.profile_cli carica")


def _wait_idle(timeout: int = 240) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = _run("gh", "run", "list", "--workflow", WORKFLOW, "-L", "5", "--json", "status")
        if not any(r["status"] in ("in_progress", "queued", "waiting", "pending") for r in json.loads(out)):
            return
        print("  ⏳ c'è un giro in corso, aspetto…")
        time.sleep(10)
    raise RuntimeError("il giro in corso non finisce: riprova fra qualche minuto")


def carica() -> None:
    if not PROFILE_FILE.exists():
        sys.exit("Non trovo profilo.json: prima lancia `python -m bot.profile_cli scarica`.")
    try:
        profile = json.loads(PROFILE_FILE.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"❌ profilo.json non è un JSON valido (riga {e.lineno}, colonna {e.colno}): {e.msg}")
    errors = _validate(profile)
    if errors:
        sys.exit("❌ Correggi profilo.json:\n- " + "\n- ".join(errors))

    print("⏸  Metto in pausa il bot…")
    _run("gh", "workflow", "disable", WORKFLOW)
    try:
        _wait_idle()
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            _fetch_state(data)
            current = _load_profile_from(data)
            if BASE_FILE.exists() and json.loads(BASE_FILE.read_text()) != current:
                sys.exit("❌ Nel frattempo il profilo è stato cambiato dal bot (comandi Telegram). "
                         "Riscaricalo con `scarica`, rifai le modifiche e ricarica.")
            _save_profile_to(data, profile)
            env = dict(os.environ, GIT_INDEX_FILE=str(data / ".index"),
                       GIT_AUTHOR_NAME="job-bot", GIT_AUTHOR_EMAIL="job-bot@users.noreply.github.com",
                       GIT_COMMITTER_NAME="job-bot", GIT_COMMITTER_EMAIL="job-bot@users.noreply.github.com")
            run = lambda *c: subprocess.run(c, cwd=ROOT, env=env, check=True, capture_output=True, text=True).stdout
            run("git", f"--work-tree={data}", "add", "-A", "-f", "--", "profile.json.enc", "state.json.enc")
            tree = run("git", "write-tree").strip()
            commit = run("git", "commit-tree", tree, "-m", "profilo modificato da file").strip()
            run("git", "push", "-q", "-f", "origin", f"{commit}:refs/heads/{BRANCH}")
        BASE_FILE.write_text(json.dumps(profile, ensure_ascii=False, indent=2))
        print("✅ Profilo caricato. Il prossimo giro userà la nuova versione.")
    finally:
        _run("gh", "workflow", "enable", WORKFLOW)
        print("▶️  Bot riattivato.")


def main() -> None:
    if not STATE_KEY:
        sys.exit("Manca STATE_KEY nel file .env.")
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "scarica":
        scarica()
    elif action == "carica":
        carica()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
