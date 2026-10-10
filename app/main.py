"""API + fichiers statiques. Pas de compte : un appareil = un identifiant et un secret générés par le navigateur."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import segno
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import engine
from .db import DB
from .poller import Poller
from .push import Pusher

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
APP_NAME = os.environ.get("APP_NAME", "Suivi Tennis")
CONTACT = os.environ.get("VAPID_CONTACT", "https://github.com/Mincka/tennis-suivi")
POLL = os.environ.get("POLL", "1") == "1"
STATIC = Path(__file__).parent / "static"
MAX_FOLLOWS = 30

DATA_DIR.mkdir(parents=True, exist_ok=True)
db = DB(DATA_DIR / "tennis.db")
pusher = Pusher(db, DATA_DIR, CONTACT)
poller = Poller(db, pusher, os.environ.get("USER_AGENT", "tennis-suivi (+https://github.com/Mincka/tennis-suivi)"))


@asynccontextmanager
async def lifespan(_app):
    task = asyncio.create_task(poller.run()) if POLL else None
    yield
    if task:
        task.cancel()


app = FastAPI(title=APP_NAME, lifespan=lifespan, docs_url=None, redoc_url=None)


# --- appareil -----------------------------------------------------------------

def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def device(x_device_id: str = Header(min_length=16, max_length=64),
           x_device_secret: str = Header(min_length=32, max_length=128),
           user_agent: str = Header("")) -> str:
    row = db.one("SELECT secret_hash FROM devices WHERE id=?", x_device_id)
    now = time.time()
    if row is None:
        db.x("INSERT INTO devices (id, secret_hash, created, last_seen, user_agent) VALUES (?,?,?,?,?)",
             x_device_id, _hash(x_device_secret), now, now, user_agent[:200])
    elif not secrets.compare_digest(row["secret_hash"], _hash(x_device_secret)):
        raise HTTPException(403, "appareil inconnu")
    else:
        db.x("UPDATE devices SET last_seen=? WHERE id=?", now, x_device_id)
    return x_device_id


class Subscription(BaseModel):
    endpoint: str = Field(max_length=1000)
    keys: dict[str, str]


class DeviceIn(BaseModel):
    subscription: Subscription | None = None


@app.put("/api/device")
def put_device(body: DeviceIn, dev: str = Depends(device)):
    sub = body.subscription.model_dump() if body.subscription else None
    if sub and not sub["endpoint"].startswith("https://"):
        raise HTTPException(400, "endpoint invalide")
    db.x("UPDATE devices SET subscription=?, push_error=NULL WHERE id=?",
         json.dumps(sub) if sub else None, dev)
    return get_device(dev)


@app.get("/api/device")
def get_device(dev: str = Depends(device)):
    r = db.one("SELECT * FROM devices WHERE id=?", dev)
    return {"subscribed": bool(r["subscription"]), "push_ok": r["push_ok"],
            "push_error": r["push_error"]}


@app.post("/api/test")
async def test_push(dev: str = Depends(device)):
    nonce = secrets.token_hex(8)
    ok = await pusher.send(dev, {"title": "🎾 Notifications actives", "kind": "test", "nonce": nonce,
                                 "body": "Vous recevrez ici les matchs de vos joueurs.",
                                 "tag": "test", "url": "/"}, ttl=120)
    r = db.one("SELECT push_error FROM devices WHERE id=?", dev)
    return {"sent": ok, "nonce": nonce, "error": r["push_error"]}


# --- suivis -------------------------------------------------------------------

class FollowIn(BaseModel):
    player_id: int
    level: str = Field(pattern="^(match|set|jeu)$")
    reminder: int | None = Field(default=None, ge=0, le=240)


def _player_status(pid: int) -> dict:
    out: dict = {}
    m = db.one("""SELECT m.*, t.name tname FROM matches m JOIN tournaments t ON t.path=m.tournament
                  WHERE (m.p1=? OR m.p2=?) AND m.status IN ('live','ending') ORDER BY m.updated DESC LIMIT 1""",
               pid, pid)
    if m:
        flip = m["p2"] == pid
        g1, g2 = json.loads(m["games1"]), json.loads(m["games2"])
        sets = list(zip(g1, g2, strict=False))
        cur = sets.pop() if m["live_set"] and sets else None
        opp = db.one("SELECT name FROM players WHERE id=?", m["p1"] if flip else m["p2"])
        out["live"] = {"score": engine._fmt_sets(sets, cur, flip), "court": m["court"],
                       "opponent": opp["name"] if opp else "?", "duration": m["duration"],
                       "serving": m["serving"] == (2 if flip else 1), "tournament": m["tname"]}
    s = db.one("""SELECT s.*, t.name tname FROM scheduled s JOIN tournaments t ON t.path=s.tournament
                  WHERE s.p1=? OR s.p2=? ORDER BY s.at LIMIT 1""", pid, pid)
    if s:
        opp = db.one("SELECT name FROM players WHERE id=?", s["p2"] if s["p1"] == pid else s["p1"])
        out["next"] = {"at": s["at"], "when": engine._when(s["at"]), "court": s["court"],
                       "opponent": opp["name"] if opp else "?", "tournament": s["tname"]}
    f = db.one("""SELECT * FROM matches WHERE (p1=? OR p2=?) AND status='finished'
                  ORDER BY updated DESC LIMIT 1""", pid, pid)
    if f:
        flip = f["p2"] == pid
        opp = db.one("SELECT name FROM players WHERE id=?", f["p1"] if flip else f["p2"])
        sets = list(zip(json.loads(f["games1"]), json.loads(f["games2"]), strict=False))
        out["last"] = {"score": engine._fmt_sets(sets, None, flip),
                       "won": None if f["winner"] is None else f["winner"] == (2 if flip else 1),
                       "opponent": opp["name"] if opp else "?"}
    return out


def _player(r) -> dict:
    ts = db.q("""SELECT t.name FROM player_tournaments pt JOIN tournaments t ON t.path=pt.tournament
                 WHERE pt.player_id=? ORDER BY t.start DESC LIMIT 3""", r["id"])
    return {"id": r["id"], "name": r["name"], "club": r["club"], "rank": r["rank"],
            "tournaments": [t["name"] for t in ts]}


@app.get("/api/follows")
def get_follows(dev: str = Depends(device)):
    rows = db.q("""SELECT f.*, p.* FROM follows f JOIN players p ON p.id=f.player_id
                   WHERE f.device_id=? ORDER BY f.created""", dev)
    return [{**_player(r), "level": r["level"], "reminder": r["reminder"],
             "status": _player_status(r["id"])} for r in rows]


@app.put("/api/follows")
def put_follows(body: list[FollowIn], dev: str = Depends(device)):
    if len(body) > MAX_FOLLOWS:
        raise HTTPException(400, f"{MAX_FOLLOWS} joueurs maximum")
    now = time.time()
    db.x("BEGIN")
    try:
        old = {r["player_id"]: r["created"] for r in db.q("SELECT * FROM follows WHERE device_id=?", dev)}
        db.x("DELETE FROM follows WHERE device_id=?", dev)
        for f in body:
            if db.one("SELECT 1 FROM players WHERE id=?", f.player_id):
                db.x("INSERT OR REPLACE INTO follows VALUES (?,?,?,?,?)", dev, f.player_id, f.level,
                     f.reminder, old.get(f.player_id, now))
        db.x("COMMIT")
    except Exception:
        db.x("ROLLBACK")
        raise
    return get_follows(dev)


# --- recherche ----------------------------------------------------------------

@app.get("/api/tournaments")
def tournaments():
    rows = db.q("""SELECT t.*, (SELECT COUNT(*) FROM player_tournaments pt WHERE pt.tournament=t.path) players
                   FROM tournaments t WHERE end >= date('now', '-3 day') ORDER BY start""")
    return [{"path": r["path"], "name": r["name"], "club": r["club"], "city": r["city"],
             "start": r["start"], "end": r["end"], "status": r["status"], "players": r["players"]}
            for r in rows]


@app.get("/api/players")
def players(q: str = "", tournament: str = "", ids: str = ""):
    if ids:
        id_list = [int(i) for i in ids.split(",")[:MAX_FOLLOWS] if i.isdigit()]
        rows = db.q(f"SELECT * FROM players WHERE id IN ({','.join('?' * len(id_list)) or '0'})", *id_list)
        return [_player(r) for r in rows]
    from .scraper import norm
    words = norm(q).split()
    sql = "SELECT DISTINCT p.* FROM players p JOIN player_tournaments pt ON pt.player_id=p.id " \
          "JOIN tournaments t ON t.path=pt.tournament WHERE t.end >= date('now', '-3 day')"
    args: list = []
    if tournament:
        sql += " AND pt.tournament=?"
        args.append(tournament)
    for w in words[:4]:
        sql += " AND (upper(p.key) LIKE ?)"
        args.append(f"%{w}%")
    if not words and not tournament:
        return []
    rows = db.q(sql + " ORDER BY p.name LIMIT 60", *args)
    return [_player(r) for r in rows]


# --- divers -------------------------------------------------------------------

@app.get("/api/config")
def config():
    return {"vapidPublicKey": pusher.public_key, "appName": APP_NAME}


@app.get("/qr.svg")
def qr(request: Request, data: str):
    # uniquement des liens vers ce site
    if urlsplit(data).netloc != request.url.netloc or len(data) > 1000:
        raise HTTPException(400)
    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="svg", scale=6, dark="#10231a", light="#ffffff", border=2,
                                     xmldecl=False)
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "max-age=3600"})


@app.get("/health")
def health():
    h = poller.health
    stale = not POLL or h["last_ok"] is None or time.time() - h["last_ok"] > 300
    body = {"ok": not stale or not POLL, **h}
    return JSONResponse(body, status_code=503 if (POLL and stale and h["last_ok"]) else 200)


@app.get("/sw.js")
def sw():
    return FileResponse(STATIC / "sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/manifest.webmanifest")
def manifest(start: str = "/"):
    if not start.startswith("/?") and start != "/":
        start = "/"
    return JSONResponse({
        "name": APP_NAME, "short_name": APP_NAME, "start_url": start, "scope": "/",
        "display": "standalone", "background_color": "#f4f1ea", "theme_color": "#1f4d3a",
        "lang": "fr", "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/static/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"}]},
        media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@app.get("/")
def index():
    html = (STATIC / "index.html").read_text(encoding="utf-8").replace("{{APP_NAME}}", APP_NAME)
    return Response(html, media_type="text/html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
