import json
import time
from pathlib import Path

import pytest

from app import engine, scraper, stats
from app.db import DB
from app.scraper import Match, Player

T = "club/tournoi"


def test_ranks_and_perfs():
    assert stats.rank_value("15/4") < stats.rank_value("15/2") < stats.rank_value("15")
    assert stats.rank_value("NC") < stats.rank_value("40") < stats.rank_value("30/5")
    assert stats.rank_value("-30") < stats.rank_value("N°60") < stats.rank_value("N°10")
    assert stats.rank_value("?") is None
    assert stats.is_perf("15/4", "15/2") and not stats.is_perf("15/2", "15/4")
    assert not stats.is_perf(None, "15/2")


def test_minutes_and_games():
    assert stats.minutes("1h38") == 98 and stats.minutes("26 min") == 26 and stats.minutes(None) is None
    assert stats.games_played([(6, 4), (4, 6), (10, 8)]) == 21  # super jeu décisif = 1 jeu


def finished(code, p1, p2, g1, g2, winner, duration, rank1="15/4", rank2="15/4", cat="SD"):
    a = Player(p1, f"{p1} TC", rank1, games=g1, winner=winner == 1)
    b = Player(p2, f"{p2} TC", rank2, games=g2, winner=winner == 2)
    m = Match(code=code, players=[a, b], category=cat, duration=duration, day="10/10")
    return m


@pytest.fixture
def db(tmp_path):
    d = DB(tmp_path / "t.db")
    d.x("INSERT INTO tournaments (path, name, start, end) VALUES (?,?,?,?)", T, "Open", "2026-10-05", "2026-10-25")
    return d


def test_tournament_and_player_insights(db):
    ms = [
        finished("A1", "MARANGE Claire", "X Anna", [6, 6], [4, 3], 1, "1h20", "15/4", "15/2"),  # perf
        finished("A2", "MARANGE Claire", "Y Bea", [4, 6, 6], [6, 3, 2], 1, "2h00"),  # remontée
        finished("A3", "Z Clara", "MARANGE Claire", [7, 6], [6, 0], 1, "1h30", "15/5", "15/4"),  # défaite
    ]
    engine.process_finished(db, T, ms, bootstrap=True)
    db.x("UPDATE matches SET stats=? WHERE code='A1'", json.dumps(
        {"services": {"total": [{"services": 6, "won": 5, "pct": 83}, {"services": 5, "won": 2, "pct": 40}]}}))
    ti = stats.tournament_insights(db, T)
    sd = ti["categories"]["SD"]
    # perfs : Claire (15/4) bat 15/2, et Z Clara (15/5) bat Claire (15/4)
    assert sd["matches"] == 3 and sd["perfs"] == 2 and sd["comebacks"] == 1 and sd["tiebreaks"] == 1
    assert sd["bagels"] == 1 and sd["three_sets_pct"] == 33
    assert ti["perfs"][0]["winner"]["name"] == "MARANGE Claire"  # plus grand écart de classement d'abord
    assert ti["longest"]["ref"] == "A2"
    assert ti["service"]["pct"] == round(100 * 7 / 11)
    pid = db.one("SELECT id FROM players WHERE name='MARANGE Claire'")["id"]
    pi = stats.player_insights(db, pid)
    assert pi["matches"] == [2, 1] and pi["sets"] == [4, 3] and pi["tiebreaks"] == [0, 1]
    assert pi["perfs"][0]["name"] == "X Anna" and pi["comebacks"] == 1
    assert pi["service"] == {"won": 5, "total": 6, "pct": 83} and pi["breaks"] == {"made": 3, "conceded": 1}
    assert pi["defeats_by_lower"] == 1


def test_match_insights_game_durations_and_runs(db):
    def live(g1, g2):
        a = Player("MARANGE Claire", "A TC", "15/4", games=g1)
        b = Player("LATRONICO Tia", "B TC", "15/4", games=g2)
        return Match(code="L1", players=[a, b], category="SD", duration="0h20", live_set=True)

    # le passage à 3-2 est manqué (redémarrage) : l'écart 2-2 -> 4-2 ne doit pas compter comme un jeu
    for i, (g1, g2) in enumerate([([0], [0]), ([0], [1]), ([0], [2]), ([1], [2]), ([2], [2]), ([4], [2])]):
        engine.process_live(db, T, [live(g1, g2)])
        db.x("UPDATE game_log SET ts = ? WHERE rowid = (SELECT MAX(rowid) FROM game_log)", 1000 + i * 240)
    row = db.one("SELECT * FROM matches WHERE code='L1'")
    page = (Path(__file__).parent / "fixtures" / "stats.html").read_text(encoding="utf-8")
    st = scraper.parse_match_stats(page)  # MARX - MENICONI : 6-2 6-2
    ins = stats.match_insights(db, row, st)
    assert ins["game_seconds"]["1:0-1"] == 240 and ins["game_seconds"]["1:2-2"] == 240
    # 2-2 -> 4-2 : deux jeux saisis d'un bloc (changement de côté), affichés comme tels
    assert ins["game_spans"] == {"1:4-2": 2}
    assert ins["longest_game"]["key"] != "1:4-2"
    assert ins["current_game_seconds"] >= time.time() - 1000 - 5 * 240 - 5
    assert ins["pace"] == round(20 / 6, 2)  # 20 min / 6 jeux
    assert ins["best_run"][1] >= 4  # série de MARX dans le jeu par jeu de la fixture


def test_burst_entries_count_as_one_reading(db):
    # cas réel (WQ3L) : 3-4 puis, au changement de côté, 3-5 et 4-5 saisis à 20 s d'écart
    def live(g1, g2):
        a = Player("MARANGE Claire", "A TC", "15/4", games=g1)
        b = Player("LATRONICO Tia", "B TC", "15/4", games=g2)
        return Match(code="L2", players=[a, b], category="SD", duration="1h00", live_set=True)

    for (g1, g2), ts in [(([5, 3], [7, 4]), 1000), (([5, 3], [7, 5]), 1623), (([5, 4], [7, 5]), 1643)]:
        engine.process_live(db, T, [live(g1, g2)])
        db.x("UPDATE game_log SET ts = ? WHERE rowid = (SELECT MAX(rowid) FROM game_log)", ts)
    ins = stats.match_insights(db, db.one("SELECT * FROM matches WHERE code='L2'"), None)
    assert ins["game_seconds"] == {"2:4-5": 643} and ins["game_spans"] == {"2:4-5": 2}
