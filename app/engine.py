"""Détection des changements (début, set, fin, programmation) et rédaction des messages."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime

from .db import DB
from .scraper import TZ, Match

LEVELS = {"match": 0, "set": 1}
REQUIRED = {"scheduled": 0, "start": 0, "end": 0, "set": 1, "reminder": None}
ENDING_TIMEOUT = 15 * 60  # au-delà, on annonce la fin avec le dernier score vu


@dataclass
class Event:
    kind: str  # scheduled / start / set / end / reminder
    tournament: str
    ref: str  # code du match ou paire (programmé)
    p1: int
    p2: int
    data: dict = field(default_factory=dict)


# --- détection ---------------------------------------------------------------

def _ids(db: DB, tournament: str, m: Match) -> tuple[int, int]:
    return db.upsert_player(m.players[0], tournament), db.upsert_player(m.players[1], tournament)


def _snapshot(m: Match) -> dict:
    a, b = m.players
    return {
        "sets": m.completed_sets(),
        "current": m.current_games(),
        "court": m.court,
        "duration": m.duration,
        "category": m.category,
        "serving": 1 if a.serving else (2 if b.serving else 0),
    }


def process_live(db: DB, tournament: str, live: list[Match]) -> list[Event]:
    events = []
    seen = set()
    for m in live:
        if not m.code:
            continue
        seen.add(m.code)
        p1, p2 = _ids(db, tournament, m)
        prev = db.get_match(tournament, m.code)
        snap = _snapshot(m)
        if prev is None or prev["status"] == "finished":
            events.append(Event("start", tournament, m.code, p1, p2, snap))
        else:
            g1, g2 = json.loads(prev["games1"]), json.loads(prev["games2"])
            prev_n = min(len(g1), len(g2)) - (1 if prev["live_set"] else 0)
            sets = snap["sets"]
            if len(sets) > prev_n:
                for i in range(max(prev_n, 0), len(sets)):
                    a, b = sets[i]
                    events.append(Event("set", tournament, m.code, p1, p2,
                                        {**snap, "set_no": i + 1, "set_winner": 1 if a > b else 2,
                                         "sets_upto": sets[: i + 1]}))
        db.save_match(tournament, m, p1, p2, "live")
        # le match a commencé : il n'est plus "programmé"
        db.x("DELETE FROM scheduled WHERE tournament=? AND pair=?", tournament, m.pair_key)
        link_match_follows(db, tournament, m)
    now = time.time()
    db.x(f"""UPDATE matches SET status='ending', left_live=? WHERE tournament=? AND status='live'
             AND code NOT IN ({','.join('?' * len(seen)) or "''"})""", now, tournament, *seen)
    return events


def link_match_follows(db: DB, tournament: str, m: Match):
    """Un match suivi quand il était programmé (paire) est ensuite identifié par son code."""
    db.x("UPDATE OR IGNORE match_follows SET ref=? WHERE tournament=? AND ref=?",
         m.code, tournament, m.pair_key)


def pending_endings(db: DB, tournament: str) -> int:
    return db.one("SELECT COUNT(*) c FROM matches WHERE tournament=? AND status='ending'",
                  tournament)["c"]


def process_finished(db: DB, tournament: str, finished: list[Match], bootstrap: bool) -> list[Event]:
    """Page des terminés : clôt les matchs sortis du direct, et signale ceux joués sans direct."""
    events = []
    now = time.time()
    for i, m in enumerate(finished):
        if not m.code:
            continue
        prev = db.get_match(tournament, m.code)
        if prev is not None and prev["status"] == "finished":
            continue
        p1, p2 = _ids(db, tournament, m)
        # la page liste du plus récent au plus ancien : on garde cet ordre
        db.save_match(tournament, m, p1, p2, "finished", finished_at=now - i / 1000)
        link_match_follows(db, tournament, m)
        if not bootstrap:
            a, b = m.players
            events.append(Event("end", tournament, m.code, p1, p2, {
                **_snapshot(m), "winner": 1 if a.winner else (2 if b.winner else None),
                "retired": 1 if a.retired else (2 if b.retired else 0)}))
        db.x("DELETE FROM scheduled WHERE tournament=? AND pair=?", tournament, m.pair_key)
    # sortis du direct depuis trop longtemps sans apparaître dans les terminés
    for r in db.q("SELECT * FROM matches WHERE tournament=? AND status='ending' AND left_live<?",
                  tournament, time.time() - ENDING_TIMEOUT):
        g1, g2 = json.loads(r["games1"]), json.loads(r["games2"])
        events.append(Event("end", tournament, r["code"], r["p1"], r["p2"], {
            "sets": list(zip(g1, g2, strict=False)), "current": None, "court": r["court"],
            "duration": r["duration"], "winner": None, "retired": 0, "unconfirmed": True}))
        db.x("UPDATE matches SET status='finished', finished_at=? WHERE tournament=? AND code=?",
             time.time(), tournament, r["code"])
    return events


def process_upcoming(db: DB, tournament: str, upcoming: list[Match], bootstrap: bool) -> list[Event]:
    events = []
    now = time.time()
    keep = []
    for m in upcoming:
        p1, p2 = _ids(db, tournament, m)
        at = m.scheduled_at.timestamp() if m.scheduled_at else None
        keep.append(m.pair_key)
        prev = db.one("SELECT * FROM scheduled WHERE tournament=? AND pair=?", tournament, m.pair_key)
        db.x("""INSERT INTO scheduled VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO UPDATE SET
                at=excluded.at, court=excluded.court""",
             tournament, m.pair_key, p1, p2, m.category, m.court, at, now)
        if not bootstrap and (prev is None or (prev["at"] != at and at)):
            events.append(Event("scheduled", tournament, m.pair_key, p1, p2, {
                "at": at, "court": m.court, "category": m.category,
                "changed": prev is not None}))
    db.x(f"DELETE FROM scheduled WHERE tournament=? AND pair NOT IN ({','.join('?' * len(keep)) or chr(39) * 2})",
         tournament, *keep)
    return events


def due_reminders(db: DB) -> list[tuple[str, Event]]:
    """(device_id, événement) pour les rappels à envoyer maintenant."""
    now = time.time()
    out = []
    rows = db.q("""SELECT s.*, f.device_id, f.reminder FROM scheduled s
                   JOIN follows f ON f.player_id IN (s.p1, s.p2)
                   WHERE f.reminder IS NOT NULL AND s.at IS NOT NULL
                   AND s.at - f.reminder * 60 <= ? AND s.at > ? - 1800
                   UNION
                   SELECT s.*, f.device_id, f.reminder FROM scheduled s
                   JOIN match_follows f ON f.tournament = s.tournament AND f.ref = s.pair
                   WHERE f.reminder IS NOT NULL AND s.at IS NOT NULL
                   AND s.at - f.reminder * 60 <= ? AND s.at > ? - 1800""", now, now, now, now)
    for r in rows:
        if db.one("SELECT 1 FROM reminders_sent WHERE device_id=? AND tournament=? AND pair=? AND at=?",
                  r["device_id"], r["tournament"], r["pair"], r["at"]):
            continue
        db.x("INSERT INTO reminders_sent VALUES (?,?,?,?)", r["device_id"], r["tournament"],
             r["pair"], r["at"])
        out.append((r["device_id"], Event("reminder", r["tournament"], r["pair"], r["p1"], r["p2"], {
            "at": r["at"], "court": r["court"], "category": r["category"],
            "minutes": r["reminder"]})))
    return out


# --- destinataires -------------------------------------------------------------

def recipients(db: DB, ev: Event) -> dict[str, list[int]]:
    """device_id -> joueurs suivis dans ce match (ids), selon le niveau choisi.

    Un appareil qui suit le match lui-même sans suivre ses joueurs reçoit une liste vide
    (message neutre)."""
    need = REQUIRED[ev.kind]
    ok = lambda level: need is None or LEVELS.get(level, 0) >= need  # noqa: E731
    out: dict[str, list[int]] = {}
    for f in db.q("SELECT device_id, player_id, level FROM follows WHERE player_id IN (?, ?)",
                  ev.p1, ev.p2):
        if ok(f["level"]):
            out.setdefault(f["device_id"], []).append(f["player_id"])
    for f in db.q("SELECT device_id, level FROM match_follows WHERE tournament=? AND ref=?",
                  ev.tournament, ev.ref):
        if ok(f["level"]):
            out.setdefault(f["device_id"], [])
    return out


# --- rédaction -----------------------------------------------------------------

def _fmt_sets(sets, current, flip):
    parts = [f"{b}-{a}" if flip else f"{a}-{b}" for a, b in sets]
    if current:
        a, b = current
        parts.append(f"({b}-{a})" if flip else f"({a}-{b})")
    return " ".join(parts) or "0-0"


def day_time(ts: float | None) -> tuple[str, str]:
    """("aujourd'hui" | "demain" | "dim. 11/10", "18h00")"""
    if not ts:
        return "", ""
    d = datetime.fromtimestamp(ts, TZ)
    days = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
    today = datetime.now(TZ).date()
    if d.date() == today:
        day = "aujourd'hui"
    elif (d.date() - today).days == 1:
        day = "demain"
    else:
        day = f"{days[d.weekday()]} {d:%d/%m}"
    return day, f"{d:%Hh%M}"


def _when(ts: float | None) -> str:
    day, hour = day_time(ts)
    return f"{day} à {hour}" if day else "horaire à confirmer"


def render(db: DB, ev: Event, followed: list[int]) -> dict:
    """Titre / texte du point de vue du joueur suivi (le premier s'il y en a deux).

    Sans joueur suivi (suivi du match lui-même), le message est neutre : « A – B »."""
    if not followed:
        return _render_neutral(db, ev)
    names = {r["id"]: r for r in db.q("SELECT id, name, club, rank FROM players WHERE id IN (?, ?)",
                                      ev.p1, ev.p2)}
    focus = ev.p1 if ev.p1 in followed else ev.p2
    other = ev.p2 if focus == ev.p1 else ev.p1
    flip = focus == ev.p2
    me, opp = names.get(focus), names.get(other)
    me_n = me["name"] if me else "?"
    opp_n = opp["name"] if opp else "?"
    opp_full = f"{opp_n} ({opp['club']}{', ' + opp['rank'] if opp and opp['rank'] else ''})" if opp else opp_n
    d = ev.data
    side = 2 if flip else 1
    court = f" · {d['court']}" if d.get("court") and "/" not in d["court"] else ""
    tag = f"{ev.tournament}:{ev.ref}"
    score = _fmt_sets(d.get("sets", []), d.get("current"), flip)

    if ev.kind == "scheduled":
        title = f"📅 {me_n} : match {'reprogrammé' if d.get('changed') else 'programmé'}"
        body = f"{_when(d.get('at')).capitalize()} contre {opp_full}{court}"
    elif ev.kind == "reminder":
        mins = d.get("minutes") or 0
        title = f"⏰ {me_n} joue {'maintenant' if mins == 0 else f'dans {mins} min'}"
        body = f"Prévu {_when(d.get('at'))} contre {opp_full}{court} (horaire indicatif)"
    elif ev.kind == "start":
        title = f"🎾 {me_n} : match commencé"
        body = f"Contre {opp_full}{court}"
        if d.get("sets") or (d.get("current") and sum(d["current"])):
            body += f" · {score}"
    elif ev.kind == "set":
        won = d["set_winner"] == side
        upto = d["sets_upto"]
        mine = sum(1 for a, b in upto if (a > b) == (side == 1))
        title = f"{'✅' if won else '❌'} {me_n} {'gagne' if won else 'perd'} le set {d['set_no']} " \
                f"{_fmt_sets(upto[-1:], None, flip)}"
        body = f"Sets {mine}-{len(upto) - mine} contre {opp_n} · {score}"
    elif ev.kind == "end":
        sets = _fmt_sets(d.get("sets", []), None, flip)
        w = d.get("winner")
        if d.get("unconfirmed") or w is None:
            title = f"🏁 {me_n} : match terminé"
            body = f"Dernier score vu {sets} contre {opp_n}"
        elif w == side:
            title = f"🏆 {me_n} gagne {sets}"
            body = f"Contre {opp_full}"
        else:
            title = f"{me_n} perd {sets}"
            body = f"Contre {opp_full}"
        if d.get("retired"):
            body += " · abandon"
        if d.get("duration"):
            body += f" · {d['duration']}"
    else:
        raise ValueError(ev.kind)
    return {"title": title, "body": body, "tag": tag, "kind": ev.kind,
            "url": f"/#/j/{focus}"}


def _render_neutral(db: DB, ev: Event) -> dict:
    names = {r["id"]: r["name"] for r in db.q("SELECT id, name FROM players WHERE id IN (?, ?)",
                                              ev.p1, ev.p2)}
    a, b = names.get(ev.p1, "?"), names.get(ev.p2, "?")
    vs = f"{a} – {b}"
    d = ev.data
    court = f" · {d['court']}" if d.get("court") and "/" not in d["court"] else ""
    score = _fmt_sets(d.get("sets", []), d.get("current"), False)
    if ev.kind == "scheduled":
        title = f"📅 {vs} : match {'reprogrammé' if d.get('changed') else 'programmé'}"
        body = f"{_when(d.get('at')).capitalize()}{court}"
    elif ev.kind == "reminder":
        mins = d.get("minutes") or 0
        title = f"⏰ {vs} {'maintenant' if mins == 0 else f'dans {mins} min'}"
        body = f"Prévu {_when(d.get('at'))}{court} (horaire indicatif)"
    elif ev.kind == "start":
        title = f"🎾 {vs} : match commencé"
        body = (court[3:] if court else "En cours") + (f" · {score}" if d.get("sets") else "")
    elif ev.kind == "set":
        upto = d["sets_upto"]
        w = d["set_winner"]
        sa = sum(1 for x, y in upto if x > y)
        title = f"Set {d['set_no']} pour {a if w == 1 else b} " \
                f"{_fmt_sets(upto[-1:], None, w == 2)}"
        body = f"{vs} · sets {sa}-{len(upto) - sa} · {score}"
    elif ev.kind == "end":
        w = d.get("winner")
        if d.get("unconfirmed") or w is None:
            title = f"🏁 {vs} : match terminé"
            body = f"Dernier score vu {_fmt_sets(d.get('sets', []), None, False)}"
        else:
            title = f"🏆 {a if w == 1 else b} bat {b if w == 1 else a} " \
                    f"{_fmt_sets(d.get('sets', []), None, w == 2)}"
            body = ("Abandon · " if d.get("retired") else "") + (d.get("duration") or "")
    else:
        raise ValueError(ev.kind)
    return {"title": title, "body": body.strip(" ·"), "tag": f"{ev.tournament}:{ev.ref}",
            "kind": ev.kind, "url": f"/#/t/{ev.tournament}"}
