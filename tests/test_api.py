import importlib
import time

import pytest
from fastapi.testclient import TestClient

from app.scraper import Match, Player


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("POLL", "0")
    import app.main as main
    main = importlib.reload(main)
    db = main.db
    db.x("INSERT INTO tournaments (path, name, start, end) VALUES ('c/t', 'Open', date('now'), date('now', '+5 day'))")
    a = Player("MARANGE Claire", "HETTANGE GRANDE TC", "15/4", games=[6, 2])
    b = Player("LATRONICO Tia", "FLORANGE TC", "15/4", games=[4, 1])
    m = Match(code="ABCD", players=[a, b], court="Court 3", live_set=True)
    p1, p2 = db.upsert_player(a, "c/t"), db.upsert_player(b, "c/t")
    db.save_match("c/t", m, p1, p2, "live")
    db.x("INSERT INTO scheduled VALUES ('c/t', 'x', ?, ?, 'SD', NULL, ?, ?)", p1, 99, time.time() + 3600, time.time())
    with TestClient(main.app) as c:
        c.headers.update({"X-Device-Id": "a" * 32, "X-Device-Secret": "b" * 64})
        c.pid = p1
        yield c


def test_search_and_follow(client):
    found = client.get("/api/players", params={"q": "marangé"}).json()
    assert [p["name"] for p in found] == ["MARANGE Claire"]
    r = client.put("/api/follows", json=[{"player_id": client.pid, "level": "set", "reminder": 15}])
    assert r.status_code == 200, r.text
    f = r.json()[0]
    assert f["level"] == "set" and f["reminder"] == 15
    assert f["status"]["live"]["score"] == "6-4 (2-1)" and f["status"]["live"]["opponent"] == "LATRONICO Tia"
    assert "next" in f["status"]


def test_device_secret_is_checked(client):
    assert client.get("/api/device").json()["subscribed"] is False
    r = client.get("/api/device", headers={"X-Device-Secret": "c" * 64})
    assert r.status_code == 403


def test_pages(client):
    page = client.get("/").text
    assert "{{" not in page and "/static/app.js?v=" in page
    assert client.get("/sw").headers["content-type"].startswith("text/javascript")
    m = client.get("/manifest.webmanifest", params={"start": "/?suivre=1.s.15"}).json()
    assert m["start_url"] == "/?suivre=1.s.15"
    assert client.get("/manifest.webmanifest", params={"start": "https://evil"}).json()["start_url"] == "/"
    assert client.get("/qr.svg", params={"data": "http://testserver/?suivre=1"}).text.startswith("<svg")
    assert client.get("/qr.svg", params={"data": "https://evil/"}).status_code == 400
