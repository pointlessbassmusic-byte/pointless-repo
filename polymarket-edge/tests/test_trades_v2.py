"""Data API v2 has no end-time filter: the last print before ts comes from
walking newest-first cursor pages, never from a later print."""
from src.trades_v2 import clear_cache, last_print_before


class _Resp:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class _Http:
    def __init__(self, pages):
        self.pages, self.calls = list(pages), []

    def get(self, url, params=None, timeout=None):
        self.calls.append(dict(params))
        return _Resp(self.pages.pop(0))


def _page(stamps, cursor):
    return {"data": [{"timestamp": t, "price": 0.5, "token_id": "Y"} for t in stamps],
            "pagination": {"next_cursor": cursor}}


def test_walks_pages_back_to_ts_and_returns_the_newest_print_at_or_before_it():
    clear_cache()
    http = _Http([_page([500, 400], "c2"), _page([300, 90, 80], None)])
    assert last_print_before(http, "0xc", 100)["timestamp"] == 90
    assert len(http.calls) == 2 and http.calls[1]["cursor"] == "c2"
    assert "end" not in http.calls[0] and http.calls[0]["condition"] == "0xc"
    # a later horizon on the same market reuses the cached pages
    assert last_print_before(http, "0xc", 450)["timestamp"] == 400 and len(http.calls) == 2


def test_never_answers_with_a_later_print():
    clear_cache()
    assert last_print_before(_Http([_page([500, 400], None)]), "0xc", 100) is None


def test_a_walk_capped_before_ts_answers_none():
    clear_cache()
    http = _Http([_page([500], "c2"), _page([400], "c3")])
    assert last_print_before(http, "0xc", 100, max_pages=2) is None and len(http.calls) == 2
