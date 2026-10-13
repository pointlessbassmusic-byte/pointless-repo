"""Sackmann upstream is gone (Sep 2026): the downloader falls through to
the archival mirror and still writes the file under the upstream name."""

import httpx

from sportsbot.data import tennis_data


def test_candidate_urls_try_upstream_then_the_mirror():
    urls = tennis_data.candidate_urls("atp", 2024)
    assert urls[0] == "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_2024.csv"
    assert urls[1].endswith("/tennis_atp/main/atp_matches_2024.csv")
    assert urls[2] == ("https://raw.githubusercontent.com/Aneeshers/tennis-sackmann-archive/"
                       "main/atp/atp_matches_2024.csv")
    assert "huggingface.co/datasets/Aneeshers/tennis-sackmann-archive" in urls[3]
    assert tennis_data.candidate_urls("wta", 2019)[2].endswith("/wta/wta_matches_2019.csv")


def test_download_year_falls_through_404s_to_the_mirror(tmp_path, monkeypatch):
    seen = []

    def fake_get(url, timeout=None, follow_redirects=False):
        seen.append(url)
        req = httpx.Request("GET", url)
        if "JeffSackmann" in url:
            return httpx.Response(404, request=req, text="404: Not Found")
        return httpx.Response(200, request=req, content=b"tourney_id,surface\n1,Hard\n")

    monkeypatch.setattr(tennis_data.httpx, "get", fake_get)
    path = tennis_data.download_year("atp", 2024, cache_dir=str(tmp_path))
    assert path == str(tmp_path / "atp_matches_2024.csv")
    assert open(path, "rb").read().startswith(b"tourney_id")
    assert len(seen) == 3 and "sackmann-archive" in seen[2]
    # cached: no second download
    seen.clear()
    assert tennis_data.download_year("atp", 2024, cache_dir=str(tmp_path)) == path and seen == []

    def all_404(url, timeout=None, follow_redirects=False):
        return httpx.Response(404, request=httpx.Request("GET", url))

    monkeypatch.setattr(tennis_data.httpx, "get", all_404)
    assert tennis_data.download_year("wta", 2030, cache_dir=str(tmp_path)) is None
