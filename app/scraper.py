"""Parsing des pages HappyScore (rendu serveur, pas d'API publique).

Pages utilisées, par tournoi `<club>/<slug>` :
- /tournoi                     liste des tournois (cartes avec data-status/dates)
- /<club>/<slug>/direct/fragment  matchs en cours (rafraîchi toutes les 15 s par le site)
- /<club>/<slug>/avenir        matchs programmés
- /<club>/<slug>/termines      matchs terminés, plus récent en premier
"""
from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

BASE_URL = "https://tennis.happyscore.net"
TZ = ZoneInfo("Europe/Paris")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().upper()


def text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def strip_decorations(name: str) -> str:
    """Retire les emojis (🏆, 🚑…) ajoutés au nom."""
    return re.sub(r"[^\w\s'.-]", "", name).strip()


def player_key(name: str, club: str) -> str:
    return f"{norm(strip_decorations(name))}|{norm(club)}"


@dataclass
class Tournament:
    path: str  # "heg57330/open-ccce-credit-mutuel-crgt-554"
    name: str
    club: str
    city: str
    status: str  # UPCOMING / ONGOING / FINISHED (valeurs du site)
    start: str  # ISO date
    end: str


@dataclass
class Player:
    name: str
    club: str
    rank: str | None = None
    winner: bool = False
    retired: bool = False
    serving: bool = False
    games: list[int] = field(default_factory=list)  # jeux par set, dernier = set en cours si live

    @property
    def key(self) -> str:
        return player_key(self.name, self.club)


@dataclass
class Match:
    code: str | None  # identifiant HappyScore (ex. "KUCY"), absent pour les matchs programmés
    players: list[Player]
    category: str = ""
    court: str | None = None
    duration: str | None = None
    scheduled_at: datetime | None = None
    live_set: bool = False  # le dernier set est en cours
    day: str | None = None  # "10/10" pour les matchs terminés

    @property
    def pair_key(self) -> str:
        return " vs ".join(sorted(p.key for p in self.players))

    def completed_sets(self) -> list[tuple[int, int]]:
        a, b = self.players[0].games, self.players[1].games
        n = min(len(a), len(b)) - (1 if self.live_set else 0)
        return [(a[i], b[i]) for i in range(max(n, 0))]

    def current_games(self) -> tuple[int, int] | None:
        if not self.live_set:
            return None
        return self.players[0].games[-1], self.players[1].games[-1]


# --- liste des tournois -----------------------------------------------------

def parse_tournaments(page: str) -> list[Tournament]:
    out = []
    for m in re.finditer(r'<article\s+class="hs-tournament-card"(.*?)</article>', page, re.S):
        body = m.group(1)
        attrs = body.split(">", 1)[0]

        def get(k, attrs=attrs):
            found = re.search(rf'{k}="([^"]*)"', attrs)
            return found.group(1) if found else ""
        club = re.search(r'class="hs-tournament-club">(.*?)</p>', body, re.S)
        name = re.search(r"<h2>(.*?)</h2>", body, re.S)
        details = [text(d) for d in re.findall(r'class="hs-tournament-detail">(.*?)</div>', body, re.S)]
        path = get("data-card-url").strip("/")
        if not path:
            continue
        out.append(Tournament(
            path=path,
            name=text(name.group(1)) if name else path,
            club=text(club.group(1)) if club else "",
            city=details[1] if len(details) > 1 else "",
            status=get("data-status"),
            start=get("data-start-date"),
            end=get("data-end-date"),
        ))
    return out


# --- scoreboards (direct / terminés) -----------------------------------------

_CLUB_RANK = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")


def _parse_player_row(row: str) -> tuple[Player, bool]:
    name_m = re.search(r'class="player-name([^"]*)"[^>]*>(.*?)</span>', row, re.S)
    club_m = re.search(r'class="player-club">(.*?)</span>', row, re.S)
    raw_name = text(name_m.group(2)) if name_m else ""
    club_raw = text(club_m.group(1)) if club_m else ""
    cr = _CLUB_RANK.match(club_raw)
    club, rank = (cr.group(1), cr.group(2)) if cr else (club_raw, None)
    scores = re.findall(r'<span class="set-score([^"]*)">\s*(\d+)\s*</span>', row)
    dot = re.search(r'class="ServiceDot([^"]*)"', row)
    return Player(
        name=strip_decorations(raw_name),
        club=club,
        rank=rank,
        winner=bool(name_m) and ("winner" in name_m.group(1) or "🏆" in raw_name),
        retired="🚑" in raw_name,
        serving=bool(dot) and "--hidden" not in dot.group(1),
        games=[int(v) for _, v in scores],
    ), any("set-encours" in cls for cls, _ in scores)


def parse_scoreboards(page: str) -> list[Match]:
    out = []
    for block in page.split('<div class="scoreboard">')[1:]:
        rows = re.split(r'<div class="player-row">', block)[1:3]
        if len(rows) != 2:
            continue
        parsed = [_parse_player_row(r) for r in rows]
        players = [p for p, _ in parsed]
        code = re.search(r'class="sb-court-pill[^"]*"\s*title="([^"]+)"', block)
        court = re.search(r'class="sb-court-text">(.*?)</span>', block, re.S)
        times = re.findall(r'<div class="sb-time">(.*?)</div>', block, re.S)
        cat = re.search(r'class="cm-category-view">(.*?)</span>', block, re.S)
        court_txt = text(court.group(1)) if court else None
        # dans les terminés, la pastille du court contient la date du match
        day = court_txt if court_txt and re.fullmatch(r"\d{2}/\d{2}", court_txt) else None
        out.append(Match(
            code=code.group(1) if code else None,
            players=players,
            category=text(cat.group(1)) if cat else "",
            court=None if day else court_txt,
            duration=text(times[-1]) if times else None,
            live_set=any(live for _, live in parsed),
            day=day,
        ))
    return out


# --- matchs programmés ------------------------------------------------------

def _parse_schedule(card: str) -> datetime | None:
    day = re.search(r'class="cm-date-pill"\s*title="\w+\s+(\d{2})/(\d{2})/(\d{4})"', card)
    hour = re.search(r'class="cm-date-time">\s*(\d{1,2})h(\d{2})', card)
    if not day:
        return None
    d, m, y = (int(x) for x in day.groups())
    hh, mm = (int(hour.group(1)), int(hour.group(2))) if hour else (0, 0)
    return datetime(y, m, d, hh, mm, tzinfo=TZ)


def parse_upcoming(page: str) -> list[Match]:
    out = []
    for card in page.split('<div class="cm-card-wrap">')[1:]:
        rows = re.findall(r'<div class="cm-player-row(.*?)(?=<div class="cm-player-row|$)', card, re.S)
        players = []
        for r in rows[:2]:
            name = re.search(r'class="cm-player-name[^"]*">(.*?)</span>', r, re.S)
            club = re.search(r'class="cm-player-meta"[^>]*>(.*?)</span>', r, re.S)
            rank = re.search(r'class="cm-rank-pill">(.*?)</span>', r, re.S)
            if name:
                players.append(Player(name=strip_decorations(text(name.group(1))),
                                      club=text(club.group(1)) if club else "",
                                      rank=text(rank.group(1)) if rank else None))
        if len(players) != 2:
            continue
        court = re.search(r'class="cm-court-text">(.*?)</span>', card, re.S)
        cat = re.search(r'class="cm-category-view">(.*?)</span>', card, re.S)
        court_txt = text(court.group(1)) if court else ""
        out.append(Match(
            code=None,
            players=players,
            category=text(cat.group(1)) if cat else "",
            court=court_txt if court_txt and court_txt != "Court" else None,
            scheduled_at=_parse_schedule(card),
        ))
    return out
