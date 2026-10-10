"""Statistiques calculées à partir des données collectées (scores, durées, classements, stats HappyScore).

Rien ici n'interroge le site : uniquement la base locale.
"""
from __future__ import annotations

import json
import re
import time

from .db import DB

# --- classements FFT ---------------------------------------------------------------
# du plus faible au plus fort ; les numérotés (N°xx, Top xx) passent au-dessus de -30
_LADDER = ["NC", "40", "30/5", "30/4", "30/3", "30/2", "30/1", "30", "15/5", "15/4", "15/3", "15/2", "15/1", "15",
           "5/6", "4/6", "3/6", "2/6", "1/6", "0", "-2/6", "-4/6", "-15", "-30"]


def rank_value(rank: str | None) -> float | None:
    if not rank:
        return None
    r = rank.strip().upper().replace(" ", "")
    if r in _LADDER:
        return float(_LADDER.index(r))
    m = re.match(r"^(?:N°|N|TOP)(\d+)$", r)
    if m:  # numéroté : plus le numéro est petit, plus c'est fort
        return len(_LADDER) + 1 - int(m.group(1)) / 10000
    return None


def is_perf(winner_rank: str | None, loser_rank: str | None) -> bool:
    w, lo = rank_value(winner_rank), rank_value(loser_rank)
    return w is not None and lo is not None and lo > w


# --- outils ------------------------------------------------------------------------

def minutes(duration: str | None) -> int | None:
    """"1h38" -> 98, "26 min" -> 26"""
    if not duration:
        return None
    m = re.match(r"^\s*(\d+)h(\d{1,2})", duration)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.match(r"^\s*(\d+)\s*min", duration)
    return int(m.group(1)) if m else None


def _sets(r) -> list[tuple[int, int]]:
    return list(zip(json.loads(r["games1"]), json.loads(r["games2"]), strict=False))


def _is_super_tb(a: int, b: int) -> bool:
    return max(a, b) >= 10  # super jeu décisif (10 points) joué à la place du 3e set


def games_played(sets) -> int:
    """Nombre de jeux, un super jeu décisif comptant pour un."""
    return sum(1 if _is_super_tb(a, b) else a + b for a, b in sets)


def pace(r) -> float | None:
    """Minutes par jeu pour un match terminé (hors abandon)."""
    mins, games = minutes(r["duration"]), games_played(_sets(r))
    return mins / games if mins and games else None


def _round(x, n=1):
    return None if x is None else round(x, n)


# --- tournoi -----------------------------------------------------------------------

def _finished(db: DB, where: str, *args):
    return db.q(f"""SELECT m.*, p1.name n1, p1.rank r1, p2.name n2, p2.rank r2 FROM matches m
                    JOIN players p1 ON p1.id = m.p1 JOIN players p2 ON p2.id = m.p2
                    WHERE m.status = 'finished' AND {where}""", *args)


def category_paces(db: DB, tournament: str) -> dict:
    """Minutes par jeu moyennes par catégorie (SM, SD…) et pour tout le tournoi."""
    acc: dict[str, list[int]] = {}
    for r in _finished(db, "m.tournament = ? AND m.retired = 0", tournament):
        mins, games = minutes(r["duration"]), games_played(_sets(r))
        if mins and games:
            for k in (r["category"] or "?", "_all"):
                t = acc.setdefault(k, [0, 0, 0])
                t[0] += mins
                t[1] += games
                t[2] += 1
    return {k: {"pace": round(v[0] / v[1], 2), "matches": v[2]} for k, v in acc.items()}


def tournament_insights(db: DB, tournament: str) -> dict:
    rows = _finished(db, "m.tournament = ?", tournament)
    cats: dict[str, dict] = {}
    perfs, longest, shortest = [], None, None
    svc_won = svc_total = 0
    with_stats = 0
    for r in rows:
        c = cats.setdefault(r["category"] or "?", {"matches": 0, "three_sets": 0, "tiebreaks": 0, "bagels": 0,
                                                   "comebacks": 0, "retired": 0, "perfs": 0, "minutes": 0,
                                                   "timed": 0, "games": 0})
        sets = _sets(r)
        c["matches"] += 1
        c["three_sets"] += len(sets) >= 3
        c["tiebreaks"] += sum(1 for a, b in sets if {a, b} == {7, 6})
        c["bagels"] += sum(1 for a, b in sets if {a, b} == {6, 0})
        c["retired"] += bool(r["retired"])
        w = r["winner"]
        if w and sets and len(sets) >= 2:
            first_a, first_b = sets[0]
            c["comebacks"] += (first_a > first_b) != (w == 1)
        if w and is_perf(r["r1"] if w == 1 else r["r2"], r["r2"] if w == 1 else r["r1"]):
            c["perfs"] += 1
            wr, lr = (r["r1"], r["r2"]) if w == 1 else (r["r2"], r["r1"])
            perfs.append({"ref": r["code"], "winner": {"id": r["p1"] if w == 1 else r["p2"],
                                                       "name": r["n1"] if w == 1 else r["n2"], "rank": wr},
                          "loser": {"id": r["p2"] if w == 1 else r["p1"], "name": r["n2"] if w == 1 else r["n1"],
                                    "rank": lr},
                          "gap": rank_value(lr) - rank_value(wr), "category": r["category"], "date": r["day"],
                          "retired": bool(r["retired"])})
        mins = minutes(r["duration"])
        if mins and not r["retired"]:
            c["minutes"] += mins
            c["timed"] += 1
            c["games"] += games_played(sets)
            entry = {"ref": r["code"], "minutes": mins, "duration": r["duration"], "names": [r["n1"], r["n2"]],
                     "score": " ".join(f"{a}-{b}" for a, b in sets), "category": r["category"]}
            if not longest or mins > longest["minutes"]:
                longest = entry
            if not shortest or mins < shortest["minutes"]:
                shortest = entry
        if r["stats"]:
            svc = (json.loads(r["stats"]).get("services") or {}).get("total")
            if svc:
                with_stats += 1
                for x in svc:
                    svc_won += x["won"]
                    svc_total += x["services"]
    out_cats = {}
    for k, c in sorted(cats.items()):
        out_cats[k] = {
            "matches": c["matches"],
            "avg_minutes": round(c["minutes"] / c["timed"]) if c["timed"] else None,
            "pace": _round(c["minutes"] / c["games"], 2) if c["games"] else None,
            "three_sets_pct": round(100 * c["three_sets"] / c["matches"]),
            "tiebreaks": c["tiebreaks"], "bagels": c["bagels"], "comebacks": c["comebacks"],
            "retired": c["retired"], "perfs": c["perfs"],
        }
    perfs.sort(key=lambda p: -p["gap"])
    return {
        "categories": out_cats,
        "perfs": perfs,
        "longest": longest,
        "shortest": shortest,
        "service": {"won": svc_won, "total": svc_total, "matches": with_stats,
                    "pct": round(100 * svc_won / svc_total) if svc_total else None,
                    "breaks_per_match": _round((svc_total - svc_won) / with_stats) if with_stats else None},
    }


# --- joueur ------------------------------------------------------------------------

def player_insights(db: DB, pid: int) -> dict:
    rows = _finished(db, "(m.p1 = ? OR m.p2 = ?)", pid, pid)
    s = {"won": 0, "lost": 0, "sets": [0, 0], "games": [0, 0], "tiebreaks": [0, 0], "minutes": 0, "timed": 0,
         "played": 0, "service": [0, 0], "breaks": [0, 0], "perfs": [], "defeats_by_lower": 0, "comebacks": 0}
    for r in rows:
        side = 1 if r["p1"] == pid else 2
        sets = [(a, b) if side == 1 else (b, a) for a, b in _sets(r)]
        me_rank, opp_rank = (r["r1"], r["r2"]) if side == 1 else (r["r2"], r["r1"])
        opp = {"id": r["p2"] if side == 1 else r["p1"], "name": r["n2"] if side == 1 else r["n1"], "rank": opp_rank}
        if r["winner"] == side:
            s["won"] += 1
            if is_perf(me_rank, opp_rank):
                s["perfs"].append({**opp, "ref": r["code"], "tournament": r["tournament"]})
            if sets and sets[0][0] < sets[0][1]:
                s["comebacks"] += 1
        elif r["winner"]:
            s["lost"] += 1
            s["defeats_by_lower"] += is_perf(opp_rank, me_rank)
        for a, b in sets:
            s["sets"][0 if a > b else 1] += 1
            if {a, b} == {7, 6}:
                s["tiebreaks"][0 if a > b else 1] += 1
            if not _is_super_tb(a, b):
                s["games"][0] += a
                s["games"][1] += b
        mins = minutes(r["duration"])
        if mins and not r["retired"]:
            s["minutes"] += mins
            s["timed"] += 1
            s["played"] += games_played(sets)
        if r["stats"]:
            svc = (json.loads(r["stats"]).get("services") or {}).get("total")
            if svc and len(svc) == 2:
                me, other = svc[side - 1], svc[2 - side]
                s["service"][0] += me["won"]
                s["service"][1] += me["services"]
                s["breaks"][0] += other["services"] - other["won"]  # breaks réalisés
                s["breaks"][1] += me["services"] - me["won"]  # breaks concédés
    total_games = sum(s["games"])
    return {
        "matches": [s["won"], s["lost"]],
        "sets": s["sets"],
        "games": s["games"],
        "games_pct": round(100 * s["games"][0] / total_games) if total_games else None,
        "tiebreaks": s["tiebreaks"],
        "avg_minutes": round(s["minutes"] / s["timed"]) if s["timed"] else None,
        "pace": _round(s["minutes"] / s["played"], 2) if s["played"] else None,
        "service": {"won": s["service"][0], "total": s["service"][1],
                    "pct": round(100 * s["service"][0] / s["service"][1]) if s["service"][1] else None},
        "breaks": {"made": s["breaks"][0], "conceded": s["breaks"][1]},
        "perfs": s["perfs"],
        "comebacks": s["comebacks"],
        "defeats_by_lower": s["defeats_by_lower"],
    }


# --- match -------------------------------------------------------------------------

def match_insights(db: DB, r, stats: dict | None) -> dict:
    """Rythme, durée des jeux (d'après notre suivi du direct), séries et remontées."""
    out: dict = {}
    live = r["status"] in ("live", "ending")
    sets = _sets(r)
    mins = minutes(r["duration"])
    games = games_played(sets)
    out["pace"] = _round(mins / games, 2) if mins and games else None
    paces = category_paces(db, r["tournament"])
    out["avg_pace_category"] = paces.get(r["category"] or "?", {}).get("pace")
    out["avg_pace_category_matches"] = paces.get(r["category"] or "?", {}).get("matches")
    out["avg_pace_tournament"] = paces.get("_all", {}).get("pace")

    # durée des jeux : écart entre deux changements de score vus en direct
    log = db.q("SELECT games1, games2, ts FROM game_log WHERE tournament=? AND code=? ORDER BY ts",
               r["tournament"], r["code"])
    durations = {}
    spans = {}  # nombre de jeux couverts quand le score a été saisi en bloc (souvent aux changements de côté)
    total = lambda e: sum(json.loads(e["games1"])) + sum(json.loads(e["games2"]))  # noqa: E731
    for prev, cur in zip(log, log[1:], strict=False):
        # HappyScore est souvent mis à jour aux changements de côté : deux jeux d'un coup.
        # Au-delà de deux jeux (relevé manqué), la durée n'aurait plus de sens.
        n = total(cur) - total(prev)
        if n not in (1, 2):
            continue
        g1, g2 = json.loads(cur["games1"]), json.loads(cur["games2"])
        # clé "set:jeux1-jeux2" du score atteint ; un nouveau set à 0-0 marque la fin du précédent
        if g1 and g1[-1] + g2[-1] > 0:
            key = f"{len(g1)}:{g1[-1]}-{g2[-1]}"
        elif len(g1) > 1:
            key = f"{len(g1) - 1}:{g1[-2]}-{g2[-2]}"
        else:
            continue
        durations[key] = round(cur["ts"] - prev["ts"])
        if n > 1:
            spans[key] = n
    out["game_seconds"] = durations
    out["game_spans"] = spans
    if log and live:
        out["current_game_seconds"] = round(time.time() - log[-1]["ts"])
        first = (json.loads(log[0]["games1"]), json.loads(log[0]["games2"]))
        out["tracked_since_start"] = first in (([0], [0]), ([], []))
    single = {k: v for k, v in durations.items() if k not in spans}
    if single:
        k, v = max(single.items(), key=lambda kv: kv[1])
        out["longest_game"] = {"key": k, "seconds": v}

    # séries et remontées, à partir du jeu par jeu HappyScore
    seq = (stats or {}).get("games") or []
    best = {1: 0, 2: 0}
    run_side, run = 0, 0
    deficit = {1: 0, 2: 0}  # plus grand retard comblé dans un set
    prev_set, prev_score = None, (0, 0)
    max_behind = {1: 0, 2: 0}
    for g in seq:
        if g["set"] != prev_set:
            if prev_set is not None:
                _close_set(prev_score, max_behind, deficit)
            prev_set, prev_score, max_behind = g["set"], (0, 0), {1: 0, 2: 0}
        a, b = g["score"]
        winner = 1 if a > prev_score[0] else 2
        prev_score = (a, b)
        run = run + 1 if winner == run_side else 1
        run_side = winner
        best[winner] = max(best[winner], run)
        max_behind[1] = max(max_behind[1], b - a)
        max_behind[2] = max(max_behind[2], a - b)
    if prev_set is not None:
        _close_set(prev_score, max_behind, deficit, ongoing=live)
    out["best_run"] = best
    out["current_run"] = {"side": run_side, "games": run} if seq else None
    out["comeback"] = deficit
    return out


def _close_set(score, max_behind, deficit, ongoing=False):
    """Retard comblé : mené de N jeux puis revenu à égalité ou devant."""
    a, b = score
    for side, (me, other) in ((1, (a, b)), (2, (b, a))):
        if max_behind[side] >= 2 and me >= other:
            deficit[side] = max(deficit[side], max_behind[side])
