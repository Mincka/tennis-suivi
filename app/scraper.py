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
    phase: str | None = None  # "Finale", "1/2 finale"… quand le club la renseigne
    age: str | None = None  # "Sénior", "+55", "13/14 ans"…
    video: str | None = None  # page HappyScore du direct vidéo (HappyCam), si le court est filmé

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

def _opt(block: str, cls: str) -> str | None:
    m = re.search(rf'class="{cls}">(.*?)</span>', block, re.S)
    return (text(m.group(1)) or None) if m else None


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
        cam = re.search(r'class="happycam-round-btn"', block)
        video = cam and re.search(r'href="([^"]*/video/live/match/[^"]+)"', block)
        out.append(Match(
            code=code.group(1) if code else None,
            players=players,
            category=text(cat.group(1)) if cat else "",
            court=None if day else court_txt,
            duration=text(times[-1]) if times else None,
            live_set=any(live for _, live in parsed),
            day=day,
            phase=_opt(block, "cm-phase-view"),
            age=_opt(block, "cm-age-view"),
            video=_media(video.group(1)) if video else None,
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
            phase=_opt(card, "cm-phase-view"),
            age=_opt(card, "cm-age-view"),
        ))
    return out


# --- page d'accueil d'un tournoi ------------------------------------------------

def _media(src: str | None) -> str | None:
    if not src:
        return None
    return src if src.startswith("http") else BASE_URL + src


def parse_tournament_page(page: str) -> dict:
    """Infos de la page d'un tournoi : édition, visuels, surface, dotation, juge-arbitre, météo, chiffres."""
    info: dict = {}
    ed = re.search(r'class="hs-tour-edition">(.*?)<', page, re.S)
    info["edition"] = text(ed.group(1)) if ed else None
    logo = re.search(r'class="hs-tour-hero-logo".*?<img\s+src="([^"]+)"', page, re.S)
    info["logo"] = _media(logo.group(1)) if logo else None
    poster = re.search(r'class="hs-tour-poster".*?<img src="([^"]+)"', page, re.S)
    info["poster"] = _media(poster.group(1)) if poster else None

    pills = re.findall(r'<div class="hs-tour-meta-pill">(.*?)</div>', page, re.S)
    facts = [text(p) for p in pills]
    ref = re.search(r'class="hs-tour-referee-link" href="tel:([^"]+)"[^>]*>\s*<span>(.*?)</span>', page, re.S)
    info["referee"] = {"name": text(ref.group(2)), "phone": ref.group(1)} if ref else None
    # pastilles : dates, lieu, surface, dotation, juge-arbitre (dans cet ordre, certaines optionnelles)
    rest = [f for f in facts if f and not re.search(r"\d{4}$", f) and not re.match(r"^\d{5}\s", f)
            and not (ref and f == text(ref.group(2)))]
    info["prize"] = next((f for f in rest if "€" in f), None)
    info["surface"] = next((f for f in rest if "€" not in f), None)

    w = re.search(r'<section class="hs-tour-weather-card".*?</section>', page, re.S)
    if w:
        wb = w.group(0)
        place = re.search(r"<h2>Météo du jour</h2>\s*<p>(.*?)</p>", wb, re.S)
        cur = re.search(r'class="hs-tour-weather-current">(.*?)<', wb, re.S)
        periods = []
        for a in re.findall(r'<article class="hs-tour-weather-period">(.*?)</article>', wb, re.S):
            g = lambda k, a=a: (re.search(rf'class="{k}"[^>]*>(.*?)</span>', a, re.S) or [None, ""])[1]  # noqa: E731
            temp = re.search(r"<strong>(.*?)</strong>", a, re.S)
            periods.append({"label": text(g("hs-tour-weather-label")), "desc": text(g("hs-tour-weather-desc")),
                            "icon": text(g("hs-tour-weather-icon")), "temp": text(temp.group(1)) if temp else "",
                            "details": [text(s) for s in re.findall(r"<small>(.*?)</small>", a, re.S)]})
        info["weather"] = {"place": text(place.group(1)) if place else "", "current": text(cur.group(1)) if cur else "",
                           "periods": periods}

    figures = [{"value": text(v), "label": text(lbl)} for v, lbl in
               re.findall(r'<article class="hs-tour-figure[^"]*">.*?<strong>(.*?)</strong>\s*<span>(.*?)</span>',
                          page, re.S)]
    shares = [{"label": text(lbl), "count": text(c), "pct": text(p)} for lbl, c, p in
              re.findall(r'<article class="hs-tour-progress-stat"[^>]*>.*?<span>(.*?)<em>(.*?)</em></span>\s*'
                         r'<strong>(.*?)</strong>', page, re.S)]
    info["figures"] = figures
    info["shares"] = shares
    return info


# --- page de statistiques d'un match --------------------------------------------

STATS_VERSION = 2  # à incrémenter quand le parseur extrait de nouvelles données (relecture des feuilles)
_TS = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"


def _local_ts(s: str | None) -> float | None:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ).timestamp()


def parse_match_stats(page: str) -> dict:
    """Résumé (durées, heures réelles), jeu par jeu (serveur, break) et services gagnés, par joueur."""
    out: dict = {"v": STATS_VERSION}
    # heures réelles de début et de fin : présentes en commentaire dans la page (heure locale)
    ms = re.search(rf"Match startDate={_TS}[^ ]*\s+endDate=(?:{_TS})?", page)
    out["started_at"] = _local_ts(ms.group(1)) if ms else None
    out["ended_at"] = _local_ts(ms.group(2)) if ms else None
    out["set_times"] = [[_local_ts(a), _local_ts(b)] for _, a, b in
                        re.findall(rf"SET #(\d+) startDate={_TS}[^ ]*\s+endDate=(?:{_TS})?", page)]
    sched = re.search(r'class="sh-meta-datetime">\s*\w+\s+(\d{2})/(\d{2})/(\d{4})\s+(\d{1,2}):(\d{2})', page)
    out["scheduled_at"] = (datetime(int(sched.group(3)), int(sched.group(2)), int(sched.group(1)),
                                    int(sched.group(4)), int(sched.group(5)), tzinfo=TZ).timestamp()
                           if sched else None)
    cat = re.search(r'class="sh-meta-category">(.*?)</span>', page, re.S)
    dt = re.search(r'class="sh-meta-datetime">(.*?)</span>', page, re.S)
    out["category"] = text(cat.group(1)) if cat else None
    out["datetime"] = text(dt.group(1)) if dt else None
    court = re.search(r'<div class="rh name">(.*?)</div>', page, re.S)
    out["court"] = text(court.group(1)) if court else None
    times = re.search(r'<div class="resume-times">(.*?)</div>', page, re.S)
    if times:
        tb = re.sub(r"<!--.*?-->", "", times.group(1), flags=re.S)
        total = re.search(r'class="val-set">(.*?)</span>', tb, re.S)
        sets = [text(v) for v in re.findall(r'class="val-s\d"\s*>(.*?)</span>', tb, re.S)]
        out["durations"] = {"total": text(total.group(1)) if total else None, "sets": [s for s in sets if s]}

    games = []
    for chunk in page.split('<div class="pp-game" data-set="')[1:]:
        set_no = chunk.split('"', 1)[0]
        body = chunk.split("</section>", 1)[0]
        serves = re.findall(r'<div class="pp-serve">(.*?)</div>', body, re.S)[:2]
        score = re.search(r'class="pp-score">\s*(\d+)\s*-\s*(\d+)\s*<', body)
        if not score or len(serves) != 2:
            continue
        server = 1 if "pp-serve-icon" in serves[0] else 2 if "pp-serve-icon" in serves[1] else 0
        # un abandon ajoute une ligne sans serveur qui répète le dernier score
        if not server and games and games[-1]["score"] == [int(score.group(1)), int(score.group(2))]:
            continue
        srv_html = serves[0] if server == 1 else serves[1] if server == 2 else ""
        games.append({"set": int(set_no), "score": [int(score.group(1)), int(score.group(2))],
                      "server": server, "break": "tennisballred" in srv_html})
    out["games"] = games

    services = {}
    for scope, body in re.findall(r'<div class="pie-grid pie-group" data-set="([^"]+)"[^>]*>(.*?)'
                                  r'(?=<div class="pie-grid pie-group"|</section>)', page, re.S):
        cards = []
        for c in re.findall(r'<div class="pie-card">(.*?)<div class="pie-legend">', body, re.S):
            total = re.search(r'class="pie-sub">.*?(\d+)\s*</span>', c, re.S)
            pct = re.search(r'class="pie-center-top">\s*(\d+)%', c)
            won = re.search(r'class="pie-center-sub">\s*(\d+)', c)
            cards.append({"services": int(total.group(1)) if total else 0, "won": int(won.group(1)) if won else 0,
                          "pct": int(pct.group(1)) if pct else 0})
        if len(cards) == 2:
            services[scope] = cards
    out["services"] = services
    return out
