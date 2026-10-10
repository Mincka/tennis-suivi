from datetime import datetime
from pathlib import Path

from app import scraper

FIX = Path(__file__).parent / "fixtures"


def read(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_tournaments():
    ts = scraper.parse_tournaments(read("tournois.html"))
    assert len(ts) == 12
    heg = next(t for t in ts if t.path == "heg57330/open-ccce-credit-mutuel-crgt-554")
    assert heg.name == "OPEN CCCE CREDIT MUTUEL CRGT"
    assert heg.club == "HETTANGE GRANDE TC"
    assert (heg.status, heg.start, heg.end) == ("ONGOING", "2026-10-05", "2026-10-25")


def test_live():
    ms = scraper.parse_scoreboards(read("direct.html"))
    assert [m.code for m in ms] == ["2KNB", "KUCY"]
    m = ms[1]
    a, b = m.players
    assert (a.name, a.club, a.rank) == ("KHERBOUCHE PERES Elias", "THIONVILLE MOSELLE TC", "15/5")
    assert m.live_set and m.completed_sets() == [(5, 7), (6, 2)] and m.current_games() == (0, 1)
    assert b.serving and not a.serving
    assert m.court == "Court 6" and m.duration == "1h49"


def test_finished():
    ms = scraper.parse_scoreboards(read("termines.html"))
    assert len(ms) == 39
    first = ms[0]
    assert first.code == "GKLK" and not first.live_set
    assert first.day == "10/10" and first.court is None
    assert first.players[1].winner and first.players[1].name == "MULLER Thomas"
    assert first.completed_sets() == [(5, 7), (6, 2), (3, 6)]
    retired = next(m for m in ms if m.code == "NQI9")
    assert retired.players[0].retired and retired.players[1].winner


def test_upcoming():
    ms = scraper.parse_upcoming(read("avenir.html"))
    assert len(ms) == 11
    claire = next(m for m in ms if m.players[0].name == "MARANGE Claire")
    assert claire.players[1].name == "LATRONICO Tia"
    assert claire.players[0].club == "HETTANGE GRANDE TC" and claire.players[0].rank == "15/4"
    assert claire.scheduled_at.isoformat() == "2026-10-10T18:00:00+02:00"
    assert claire.category == "SD" and claire.court is None


def test_player_key_ignores_accents_and_emojis():
    assert scraper.player_key("MENICONI Maël 🏆", "GUENANGE TC") == "MENICONI MAEL|GUENANGE TC"


def test_stats_page_drops_retirement_ghost_line():
    page = read("stats.html").replace(
        '<!-- STATS (camemberts par joueur) -->',
        '<div class="pp-game" data-set="2"><div class="pp-line"><div class="pp-serve"> </div>'
        '<div class="pp-score"> 6 -  2 </div><div class="pp-serve"> </div></div></div>'
        '<!-- STATS (camemberts par joueur) -->')
    games = scraper.parse_match_stats(page)["games"]
    assert len(games) == 16 and games[-1]["server"]


def test_stats_page_real_times():
    s = scraper.parse_match_stats(read("stats.html"))
    hm = lambda ts: datetime.fromtimestamp(ts, scraper.TZ).strftime("%H:%M")  # noqa: E731
    assert (hm(s["scheduled_at"]), hm(s["started_at"]), hm(s["ended_at"])) == ("16:00", "16:29", "17:40")
    assert [hm(a) for a, _ in s["set_times"]] == ["16:29", "17:11"] and s["v"] == scraper.STATS_VERSION
