from __future__ import annotations

import requests

from .config import USER_AGENT

_session = requests.Session()
_session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
})


class RateLimited(Exception):
    """Il sito ha risposto 429/999: smettiamo di interrogarlo per questo giro."""


def get(url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", 12)
    resp = _session.get(url, **kwargs)
    if resp.status_code in (429, 999):
        raise RateLimited(f"{resp.status_code} da {url.split('?')[0]}")
    resp.raise_for_status()
    return resp
