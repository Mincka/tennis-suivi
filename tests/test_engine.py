import time
from datetime import datetime, timedelta

import pytest

from app import engine
from app.db import DB
from app.scraper import TZ, Match, Player

T = "club/tournoi"


def match(g1, g2, live=True, code="ABCD", w=None):
    a = Player("MARANGE Claire", "HETTANGE GRANDE TC", "15/4", games=list(g1), winner=w == 1)
    b = Player("LATRONICO Tia", "FLORANGE TC", "15/4", games=list(g2), winner=w == 2)
    return Match(code=code, players=[a, b], category="SD", court="Court 3", duration="0h30", live_set=live)


@pytest.fixture
def db(tmp_path):
    d = DB(tmp_path / "t.db")
    d.x("INSERT INTO tournaments (path, name, start, end) VALUES (?,?,?,?)", T, "Open", "2026-10-05", "2026-10-25")
    return d


def follow(db, level, reminder=None, device="dev1", name="MARANGE Claire"):
    db.x("INSERT OR IGNORE INTO devices (id, secret_hash) VALUES (?, 'x')", device)
    pid = db.one("SELECT id FROM players WHERE name=?", name)["id"]
    db.x("INSERT OR REPLACE INTO follows VALUES (?,?,?,?,?)", device, pid, level, reminder, time.time())
    return pid


def kinds(evs):
    return [e.kind for e in evs]


def test_match_lifecycle(db):
    evs = engine.process_live(db, T, [match([0], [0])])
    assert kinds(evs) == ["start"]
    follow(db, "set")
    assert engine.process_live(db, T, [match([0], [0])]) == []
    # un jeu ne notifie plus
    assert engine.process_live(db, T, [match([1], [0])]) == []
    # fin du set 1 (6-4), nouveau set à 0-0
    evs = engine.process_live(db, T, [match([6, 0], [4, 0])])
    assert kinds(evs) == ["set"] and evs[0].data["set_winner"] == 1
    msg = engine.render(db, evs[0], [evs[0].p1])
    assert msg["title"] == "✅ MARANGE Claire gagne le set 1 6-4"
    assert msg["body"].startswith("Sets 1-0 contre LATRONICO Tia")
    engine.process_live(db, T, [match([6, 3], [4, 6])])  # set 2 perdu
    # le match quitte le direct : en attente des terminés
    assert engine.process_live(db, T, []) == []
    assert engine.pending_endings(db, T) == 1
    evs = engine.process_finished(db, T, [match([6, 3, 10], [4, 6, 8], live=False, w=1)], bootstrap=False)
    assert kinds(evs) == ["end"]
    msg = engine.render(db, evs[0], [evs[0].p1])
    assert msg["title"] == "🏆 MARANGE Claire gagne 6-4 3-6 10-8"
    # du point de vue de l'adversaire, le score est inversé
    msg = engine.render(db, evs[0], [evs[0].p2])
    assert msg["title"] == "LATRONICO Tia perd 4-6 6-3 8-10"
    assert engine.process_finished(db, T, [match([6, 3, 10], [4, 6, 8], live=False, w=1)], False) == []


def test_bootstrap_finished_is_silent(db):
    assert engine.process_finished(db, T, [match([6, 6], [0, 0], live=False, w=1)], bootstrap=True) == []


def test_ending_timeout(db):
    engine.process_live(db, T, [match([6, 2], [4, 1])])
    engine.process_live(db, T, [])
    db.x("UPDATE matches SET left_live=?", time.time() - engine.ENDING_TIMEOUT - 1)
    evs = engine.process_finished(db, T, [], bootstrap=False)
    assert kinds(evs) == ["end"] and evs[0].data["unconfirmed"]


def test_recipients_by_level(db):
    ev = engine.process_live(db, T, [match([0], [0])])[0]
    follow(db, "match", device="a")
    follow(db, "set", device="b")
    set_end = engine.process_live(db, T, [match([6, 0], [3, 0])])[0]
    assert set(engine.recipients(db, ev)) == {"a", "b"}
    assert set(engine.recipients(db, set_end)) == {"b"}


def test_scheduled_and_reminder(db):
    at = datetime.now(TZ) + timedelta(minutes=10)
    up = match([], [], live=False, code=None)
    up.scheduled_at = at
    assert engine.process_upcoming(db, T, [up], bootstrap=True) == []
    pid = follow(db, "match", reminder=15)
    # rappel 15 min avant : dû maintenant, une seule fois
    due = engine.due_reminders(db)
    assert [(d, e.kind) for d, e in due] == [("dev1", "reminder")]
    assert engine.due_reminders(db) == []
    msg = engine.render(db, due[0][1], [pid])
    assert msg["title"] == "⏰ MARANGE Claire joue dans 15 min"
    # horaire modifié -> nouvelle notification de programmation
    up.scheduled_at = at + timedelta(hours=1)
    evs = engine.process_upcoming(db, T, [up], bootstrap=False)
    assert kinds(evs) == ["scheduled"] and evs[0].data["changed"]
    # le match commence : il disparaît des programmés
    engine.process_live(db, T, [match([0], [0])])
    assert db.one("SELECT COUNT(*) c FROM scheduled")["c"] == 0
