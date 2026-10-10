"""Stockage SQLite. Une seule connexion, utilisée depuis la boucle asyncio."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tournaments (
    path TEXT PRIMARY KEY, name TEXT, club TEXT, city TEXT, status TEXT,
    start TEXT, end TEXT, updated REAL,
    boot_avenir INTEGER DEFAULT 0, boot_termines INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, name TEXT, club TEXT,
    rank TEXT, last_seen REAL
);
CREATE TABLE IF NOT EXISTS player_tournaments (
    player_id INTEGER, tournament TEXT, last_seen REAL,
    PRIMARY KEY (player_id, tournament)
);
-- matchs vus en direct ou terminés (identifiés par leur code HappyScore)
CREATE TABLE IF NOT EXISTS matches (
    tournament TEXT, code TEXT, p1 INTEGER, p2 INTEGER, category TEXT, court TEXT,
    games1 TEXT, games2 TEXT, live_set INTEGER, serving INTEGER, duration TEXT,
    status TEXT,            -- live / ending / finished
    winner INTEGER, retired INTEGER, left_live REAL, updated REAL,
    PRIMARY KEY (tournament, code)
);
-- matchs programmés (pas de code avant le début)
CREATE TABLE IF NOT EXISTS scheduled (
    tournament TEXT, pair TEXT, p1 INTEGER, p2 INTEGER, category TEXT, court TEXT,
    at REAL, seen REAL, PRIMARY KEY (tournament, pair)
);
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY, secret_hash TEXT, subscription TEXT, created REAL,
    last_seen REAL, push_ok REAL, push_error TEXT, user_agent TEXT
);
CREATE TABLE IF NOT EXISTS follows (
    device_id TEXT, player_id INTEGER, level TEXT, reminder INTEGER, created REAL,
    PRIMARY KEY (device_id, player_id)
);
-- suivi d'un match précis : ref = paire tant qu'il est programmé, puis code HappyScore
CREATE TABLE IF NOT EXISTS match_follows (
    device_id TEXT, tournament TEXT, ref TEXT, level TEXT, reminder INTEGER, created REAL,
    PRIMARY KEY (device_id, tournament, ref)
);
-- chronologie des scores vue en direct (le site ne publie pas l'heure des jeux) :
-- une ligne à chaque changement de jeux, horodatée à la lecture (précision ~20 s)
CREATE TABLE IF NOT EXISTS game_log (
    tournament TEXT, code TEXT, games1 TEXT, games2 TEXT, ts REAL
);
CREATE INDEX IF NOT EXISTS game_log_match ON game_log (tournament, code, ts);
CREATE TABLE IF NOT EXISTS reminders_sent (
    device_id TEXT, tournament TEXT, pair TEXT, at REAL,
    PRIMARY KEY (device_id, tournament, pair, at)
);
"""


class DB:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(matches)")}
        if "day" not in cols:
            self.conn.execute("ALTER TABLE matches ADD COLUMN day TEXT")
            self.conn.execute("""UPDATE matches SET day=court, court=NULL
                                 WHERE court GLOB '[0-9][0-9]/[0-9][0-9]'""")
        if "info" not in {r[1] for r in self.conn.execute("PRAGMA table_info(tournaments)")}:
            self.conn.execute("ALTER TABLE tournaments ADD COLUMN info TEXT")
        if "stats" not in cols:  # statistiques HappyScore d'un match terminé (JSON, figé)
            self.conn.execute("ALTER TABLE matches ADD COLUMN stats TEXT")
        if "finished_at" not in cols:
            self.conn.execute("ALTER TABLE matches ADD COLUMN finished_at REAL")
            # page des terminés lue du plus récent au plus ancien : un rowid plus petit = plus récent
            self.conn.execute("""UPDATE matches SET finished_at = updated - rowid / 1000.0
                                 WHERE status='finished'""")
        # niveau "jeu" supprimé : ramené au niveau le plus proche
        self.conn.execute("UPDATE follows SET level='set' WHERE level NOT IN ('match', 'set')")

    def q(self, sql: str, *args) -> list[sqlite3.Row]:
        return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, *args) -> sqlite3.Row | None:
        return self.conn.execute(sql, args).fetchone()

    def x(self, sql: str, *args) -> sqlite3.Cursor:
        return self.conn.execute(sql, args)

    # --- joueurs ------------------------------------------------------------
    def upsert_player(self, p, tournament: str) -> int:
        now = time.time()
        self.x("""INSERT INTO players (key, name, club, rank, last_seen) VALUES (?,?,?,?,?)
                  ON CONFLICT(key) DO UPDATE SET name=excluded.name,
                  rank=COALESCE(excluded.rank, players.rank), last_seen=excluded.last_seen""",
               p.key, p.name, p.club, p.rank, now)
        pid = self.one("SELECT id FROM players WHERE key=?", p.key)["id"]
        self.x("""INSERT INTO player_tournaments VALUES (?,?,?)
                  ON CONFLICT DO UPDATE SET last_seen=excluded.last_seen""", pid, tournament, now)
        return pid

    # --- matchs -------------------------------------------------------------
    def get_match(self, tournament: str, code: str):
        return self.one("SELECT * FROM matches WHERE tournament=? AND code=?", tournament, code)

    def save_match(self, tournament, m, p1, p2, status, finished_at=None):
        a, b = m.players
        serving = 1 if a.serving else (2 if b.serving else 0)
        winner = 1 if a.winner else (2 if b.winner else None)
        self.x("""INSERT INTO matches (tournament, code, p1, p2, category, court, games1, games2,
                  live_set, serving, duration, status, winner, retired, updated, day, finished_at)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT DO UPDATE SET games1=excluded.games1, games2=excluded.games2,
                  live_set=excluded.live_set, serving=excluded.serving, duration=excluded.duration,
                  status=excluded.status, winner=excluded.winner, retired=excluded.retired,
                  court=COALESCE(excluded.court, matches.court), updated=excluded.updated,
                  day=COALESCE(excluded.day, matches.day),
                  finished_at=COALESCE(matches.finished_at, excluded.finished_at), left_live=NULL""",
               tournament, m.code, p1, p2, m.category, m.court, json.dumps(a.games),
               json.dumps(b.games), int(m.live_set), serving, m.duration, status, winner,
               int(a.retired or b.retired), time.time(), m.day, finished_at)
