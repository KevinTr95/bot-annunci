"""Client minimo per la Bot API di Telegram."""
from __future__ import annotations

from typing import List, Optional

import requests

API = "https://api.telegram.org/bot{token}/{method}"
FILE = "https://api.telegram.org/file/bot{token}/{path}"
MAX_LEN = 4000


class Telegram:
    def __init__(self, token: str):
        self.token = token

    def _call(self, method: str, **params):
        r = requests.post(API.format(token=self.token, method=method), json=params, timeout=20)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method}: {data.get('description')}")
        return data["result"]

    def get_updates(self, offset: int) -> List[dict]:
        return self._call("getUpdates", offset=offset, timeout=0, allowed_updates=["message"])

    def send(self, chat_id, text: str, button: Optional[tuple] = None, silent: bool = False) -> None:
        """Testo HTML; button = (etichetta, url) per un pulsante sotto il messaggio."""
        chunks = [text[i:i + MAX_LEN] for i in range(0, len(text), MAX_LEN)] or [""]
        for i, chunk in enumerate(chunks):
            params = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML",
                      "disable_web_page_preview": True, "disable_notification": silent}
            if button and i == len(chunks) - 1:
                params["reply_markup"] = {"inline_keyboard": [[{"text": button[0], "url": button[1]}]]}
            self._call("sendMessage", **params)

    def download(self, file_id: str) -> bytes:
        path = self._call("getFile", file_id=file_id)["file_path"]
        r = requests.get(FILE.format(token=self.token, path=path), timeout=30)
        r.raise_for_status()
        return r.content
