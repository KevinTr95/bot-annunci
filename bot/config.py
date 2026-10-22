"""Configurazione letta da variabili d'ambiente (o da un file .env in locale)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.environ.get("BOT_DATA_DIR", str(ROOT / "data")))
PROFILE_PATH = DATA_DIR / "profile.json"
STATE_PATH = DATA_DIR / "state.json"
DEFAULT_PROFILE_PATH = ROOT / "bot" / "default_profile.json"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
# Se vuoto, il primo utente che scrive /start al bot ne diventa il proprietario.
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Chiave Fernet per cifrare profilo e stato (il repo è pubblico). Senza chiave: file in chiaro (sviluppo locale).
STATE_KEY = os.environ.get("STATE_KEY", "")

# Log dettagliati (titoli e aziende degli annunci scartati): solo in locale, mai nei log pubblici.
VERBOSE = os.environ.get("BOT_VERBOSE", "") == "1"

TIMEZONE = "Europe/Rome"

# Secondi massimi per raccolta + dettagli: su GitHub Actions ogni giro deve
# restare sotto il minuto fatturato. Quello che non si fa in tempo passa al giro dopo.
RUN_BUDGET_SECONDS = int(os.environ.get("RUN_BUDGET_SECONDS", "35"))

# Notifiche massime per giro; le altre finiscono in un messaggio riassuntivo.
MAX_NOTIFICATIONS_PER_RUN = 12

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)
