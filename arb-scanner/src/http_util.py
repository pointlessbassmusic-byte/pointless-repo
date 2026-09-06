"""Shared requests.Session factory with retry/backoff on transient failures."""
from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def retrying_session(allow_post: bool = False, total: int = 3, backoff: float = 1.0) -> requests.Session:
    """Session that retries 429/5xx with exponential backoff.

    POST retries are opt-in: only enable them for read-only POST endpoints
    (e.g. CLOB /prices) — never for order placement.
    """
    methods = ["GET", "HEAD"] + (["POST"] if allow_post else [])
    retry = Retry(
        total=total,
        backoff_factor=backoff,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(methods),
        raise_on_status=False,
    )
    s = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s
