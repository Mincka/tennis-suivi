"""Boucle de collecte : interroge HappyScore avec des fréquences adaptées et diffuse les événements."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import date, datetime, timedelta

import httpx

from . import engine, scraper
from .db import DB
from .push import Pusher

log = logging.getLogger(__name__)

# secondes entre deux lectures
EVERY_TOURNAMENTS = 30 * 60
EVERY_DIRECT = 20  # match en cours ou sur le point de commencer
EVERY_DIRECT_IDLE = 5 * 60  # tournoi en cours mais rien de prévu dans l'immédiat
EVERY_DIRECT_NIGHT = 30 * 60  # la nuit, sans match en cours
NIGHT = range(0, 7)  # heures locales
EVERY_AVENIR = 3 * 60
EVERY_TERMINES = 3 * 60
EVERY_QUIET = 15 * 60  # à venir / terminés quand aucun match ne joue
EVERY_TERMINES_PENDING = 30  # un match vient de quitter le direct
EVERY_AVENIR_UPCOMING = 60 * 60  # tournoi pas encore commencé : liste des joueurs
EVERY_INFO = 30 * 60  # page du tournoi : météo, chiffres, infos pratiques
EVERY_INFO_UPCOMING = 6 * 60 * 60
TTL = {"set": 1800, "start": 3600, "end": 6 * 3600, "scheduled": 12 * 3600,
       "reminder": 1800}


def direct_interval(db: DB, path: str, now: float | None = None) -> float:
    """Fréquence de lecture du direct : rapide seulement quand un match joue ou va commencer."""
    now = now or time.time()
    if db.one("SELECT 1 FROM matches WHERE tournament=? AND status IN ('live', 'ending')", path):
        return EVERY_DIRECT
    # match programmé dans les 30 min, ou en retard sur son horaire (jusqu'à 4 h)
    if db.one("SELECT 1 FROM scheduled WHERE tournament=? AND at BETWEEN ? AND ?", path, now - 4 * 3600, now + 1800):
        return EVERY_DIRECT
    if datetime.fromtimestamp(now, scraper.TZ).hour in NIGHT:
        return EVERY_DIRECT_NIGHT
    return EVERY_DIRECT_IDLE


class Poller:
    def __init__(self, db: DB, pusher: Pusher, user_agent: str, club_filter: str = ""):
        self.db = db
        self.pusher = pusher
        # instance dédiée à un club : seuls ses tournois sont lus et affichés
        self.clubs = {scraper.norm(c) for c in club_filter.split(";") if c.strip()}
        self.client = httpx.AsyncClient(base_url=scraper.BASE_URL, timeout=20,
                                        headers={"User-Agent": user_agent}, follow_redirects=True)
        self.next: dict[str, float] = {}
        self.health = {"last_ok": None, "last_error": None}

    async def get(self, path: str) -> str:
        r = await self.client.get(path)
        r.raise_for_status()
        return r.text

    def due(self, key: str, every: float) -> bool:
        now = time.time()
        # l'intervalle peut raccourcir (match imminent) : on n'attend pas la fin de l'ancien délai
        if self.next.get(key, 0) > now + every:
            self.next[key] = now + every
        if self.next.get(key, 0) <= now:
            self.next[key] = now + every
            return True
        return False

    def active_tournaments(self) -> tuple[list[str], list[str]]:
        today = date.today()
        live, soon = [], []
        for t in self.db.q("SELECT * FROM tournaments"):
            try:
                start, end = date.fromisoformat(t["start"]), date.fromisoformat(t["end"])
            except (TypeError, ValueError):
                continue
            if start - timedelta(days=1) <= today <= end + timedelta(days=1):
                live.append(t["path"])
            elif today < start <= today + timedelta(days=21):
                soon.append(t["path"])
        return live, soon

    async def refresh_tournaments(self):
        items = scraper.parse_tournaments(await self.get("/tournoi"))
        if self.clubs:
            items = [t for t in items if scraper.norm(t.club) in self.clubs]
            keep = [t.path for t in items]
            self.db.x(f"DELETE FROM tournaments WHERE path NOT IN ({','.join('?' * len(keep)) or chr(39) * 2})",
                      *keep)
        for t in items:
            self.db.x("""INSERT INTO tournaments (path, name, club, city, status, start, end, updated)
                         VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO UPDATE SET name=excluded.name,
                         club=excluded.club, city=excluded.city, status=excluded.status,
                         start=excluded.start, end=excluded.end, updated=excluded.updated""",
                      t.path, t.name, t.club, t.city, t.status, t.start, t.end, time.time())

    async def poll_tournament(self, path: str, live: bool) -> list[engine.Event]:
        events: list[engine.Event] = []
        t = self.db.one("SELECT * FROM tournaments WHERE path=?", path)
        if self.due(f"info:{path}", EVERY_INFO if live else EVERY_INFO_UPCOMING):
            info = scraper.parse_tournament_page(await self.get(f"/{path}"))
            self.db.x("UPDATE tournaments SET info=? WHERE path=?", json.dumps(info, ensure_ascii=False), path)
        pace = direct_interval(self.db, path) if live else None
        busy = pace == EVERY_DIRECT
        if live and self.due(f"direct:{path}", pace):
            page = await self.get(f"/{path}/direct/fragment")
            events += engine.process_live(self.db, path, scraper.parse_scoreboards(page))
        pending = engine.pending_endings(self.db, path)
        if live and (self.due(f"termines:{path}", EVERY_TERMINES if busy else EVERY_QUIET)
                     or (pending and self.due(f"termines-p:{path}", EVERY_TERMINES_PENDING))):
            page = await self.get(f"/{path}/termines")
            boot = not t["boot_termines"]
            events += engine.process_finished(self.db, path, scraper.parse_scoreboards(page), boot)
            self.db.x("UPDATE tournaments SET boot_termines=1 WHERE path=?", path)
        if self.due(f"avenir:{path}", (EVERY_AVENIR if busy else EVERY_QUIET) if live else EVERY_AVENIR_UPCOMING):
            page = await self.get(f"/{path}/avenir")
            boot = not t["boot_avenir"]
            events += engine.process_upcoming(self.db, path, scraper.parse_upcoming(page), boot)
            self.db.x("UPDATE tournaments SET boot_avenir=1 WHERE path=?", path)
        return events

    async def dispatch(self, events: list[engine.Event]):
        for ev in events:
            for device_id, followed in engine.recipients(self.db, ev).items():
                await self.pusher.send(device_id, engine.render(self.db, ev, followed), TTL[ev.kind])
        for device_id, ev in engine.due_reminders(self.db):
            followed = [r["player_id"] for r in self.db.q(
                "SELECT player_id FROM follows WHERE device_id=? AND player_id IN (?, ?)",
                device_id, ev.p1, ev.p2)]
            await self.pusher.send(device_id, engine.render(self.db, ev, followed), TTL["reminder"])

    def cleanup(self):
        month = time.time() - 30 * 86400
        self.db.x("""DELETE FROM devices WHERE subscription IS NULL AND last_seen < ?
                     AND id NOT IN (SELECT device_id FROM follows)""", month)
        self.db.x("DELETE FROM follows WHERE device_id NOT IN (SELECT id FROM devices)")
        self.db.x("DELETE FROM reminders_sent WHERE at < ?", time.time() - 7 * 86400)
        self.db.x("""DELETE FROM match_follows WHERE EXISTS (SELECT 1 FROM matches m
                     WHERE m.tournament=match_follows.tournament AND m.code=match_follows.ref
                     AND m.status='finished' AND m.updated < ?)""", time.time() - 3 * 86400)
        self.db.x("DELETE FROM match_follows WHERE device_id NOT IN (SELECT id FROM devices)")

    async def tick(self):
        if self.due("tournaments", EVERY_TOURNAMENTS):
            await self.refresh_tournaments()
            self.cleanup()
        live, soon = self.active_tournaments()
        events = []
        for path in live + soon:
            try:
                events += await self.poll_tournament(path, live=path in live)
            except Exception as e:
                self.health["last_error"] = f"{path}: {type(e).__name__}: {e}"[:300]
                log.warning("poll %s: %s", path, e)
        await self.dispatch(events)
        self.health["last_ok"] = time.time()

    async def run(self):
        while True:
            try:
                await self.tick()
            except Exception as e:
                self.health["last_error"] = f"{type(e).__name__}: {e}"[:300]
                log.exception("tick")
                # ne pas marteler le site en cas de panne
                self.next["tournaments"] = min(self.next.get("tournaments", 0), time.time() + 300)
            await asyncio.sleep(5)
