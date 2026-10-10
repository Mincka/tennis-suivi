"use strict";
// Vues : accueil (#/), tournoi (#/t/<club>/<slug>), joueur (#/j/<id>).
// core.js fournit env, api(), esc(), store, et la gestion des notifications.

const REFRESH_MS = 15000;
const LEVEL_HELP = {
  match: "Notifications quand un match est programmé, quand il commence et pour son résultat final.",
  set: "Notifications quand un match est programmé, quand il commence, à la fin de chaque set et pour le résultat final.",
};
const REMINDERS = [["", "non"], ["0", "à l'heure prévue"], ["15", "15 min avant"], ["30", "30 min avant"], ["60", "1 h avant"]];

// comparaison sans casse ni accents
const fold = (s) => (s || "").toUpperCase().normalize("NFD").replace(/\p{M}/gu, "");
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const cleanName = (s) => (s || "").replace(/[⭐️★]+/gu, "").trim();
const view = () => $("#view");

let follows = [];        // joueurs suivis (avec réglages)
let matchFollows = [];   // matchs suivis
let current = null;      // { name, render, load } de la vue affichée
let refreshTimer = null;

// ---------------------------------------------------------------------------
// Icônes
// ---------------------------------------------------------------------------
const ICON = {
  star: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8L3.5 9.7l5.9-.9z"/></svg>',
  bell: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5a2.2 2.2 0 0 0 4 0"/></svg>',
  back: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg>',
  chevron: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg>',
  pin: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.3"/></svg>',
  cal: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/></svg>',
  court: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="1.5"/><path d="M12 5v14M3 9h18M3 15h18M7 9v6M17 9v6"/></svg>',
  euro: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17.5 6.5A7 7 0 1 0 17.5 17.5M4 10h9M4 14h9"/></svg>',
  whistle: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="14" r="5"/><path d="M12.5 10.5L20 6v4l-5.5 2"/></svg>',
  phone: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/></svg>',
};

// ---------------------------------------------------------------------------
// Messages éphémères
// ---------------------------------------------------------------------------
let toastTimer = null;
function toast(message, action) {
  const el = $("#toast");
  el.innerHTML = `<span>${message}</span>`;
  if (action) {
    const b = document.createElement("button");
    b.textContent = action.label;
    b.addEventListener("click", () => { el.classList.remove("show"); action.run(); });
    el.appendChild(b);
  }
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 5000);
}
const notifHint = () => (notifState === "ok" ? "" : "<br><small>Pensez à activer les notifications.</small>");

// ---------------------------------------------------------------------------
// Suivis (joueurs et matchs)
// ---------------------------------------------------------------------------
const isFollowed = (id) => follows.some((f) => f.id === id);
const matchKey = (m) => `${m.tournament}|${m.ref}`;
const isMatchFollowed = (m) => matchFollows.some((f) => `${f.tournament}|${f.ref}` === matchKey(m));

async function loadFollows() {
  try {
    [follows, matchFollows] = await Promise.all([api("/api/follows"), api("/api/match-follows")]);
  } catch (e) { console.error(e); }
  syncShareState();
}

async function savePlayers(list) {
  follows = await api("/api/follows", { method: "PUT", body: list.map((f) => ({ player_id: f.id, level: f.level, reminder: f.reminder ?? null })) });
  syncShareState();
  rerender();
}

async function saveMatches(list) {
  matchFollows = await api("/api/match-follows", {
    method: "PUT", body: list.map((f) => ({ tournament: f.tournament, ref: f.ref, level: f.level, reminder: f.reminder ?? null })),
  });
  rerender();
}

async function togglePlayer(p) {
  const before = follows;
  try {
    if (isFollowed(p.id)) {
      await savePlayers(follows.filter((f) => f.id !== p.id));
      toast(`Vous ne suivez plus <b>${esc(p.name)}</b>`, { label: "Annuler", run: () => savePlayers(before) });
    } else {
      await savePlayers([...follows, { ...p, level: "match", reminder: 15 }]);
      toast(`Vous suivez <b>${esc(p.name)}</b>${notifHint()}`, { label: "Réglages", run: () => go(`#/j/${p.id}`) });
    }
  } catch (e) { console.error(e); toast("Échec, réessayez."); }
}

async function toggleMatch(m) {
  const before = matchFollows;
  const label = `${esc(m.players[0].name)} – ${esc(m.players[1].name)}`;
  try {
    if (isMatchFollowed(m)) {
      await saveMatches(matchFollows.filter((f) => `${f.tournament}|${f.ref}` !== matchKey(m)));
      toast(`Match ${label} retiré`, { label: "Annuler", run: () => saveMatches(before) });
    } else {
      await saveMatches([...matchFollows, { tournament: m.tournament, ref: m.ref, level: "set", reminder: m.status === "upcoming" ? 15 : null }]);
      toast(`Vous suivez le match ${label}${notifHint()}`);
    }
  } catch (e) { console.error(e); toast("Échec, réessayez."); }
}

// ---------------------------------------------------------------------------
// Carte de match (commune à toutes les vues)
// ---------------------------------------------------------------------------
function matchMeta(m, hideDay) {
  if (m.status === "upcoming") {
    const when = m.time ? (hideDay ? m.time : `${cap(m.day)} · ${m.time}`) : "Horaire à confirmer";
    const late = m.at && m.at * 1000 < Date.now();
    return `<span class="mc-when ${late ? "late" : ""}">${esc(when)}${late ? " · en attente" : ""}</span>`;
  }
  if (m.status === "live") return `<span class="pill live">En direct</span>${m.duration ? ` <span class="muted">${esc(m.duration)}</span>` : ""}`;
  return `<span class="muted">${esc([hideDay ? "" : dayLabel(m.date), m.duration].filter(Boolean).join(" · "))}</span>`;
}

function setCells(m, i) {
  const me = m.players[i].games, other = m.players[1 - i].games;
  return me.map((g, k) => {
    const cur = m.live_set && k === me.length - 1;
    const won = !cur && g > (other[k] ?? 0);
    return `<b class="${cur ? "cur" : won ? "won" : ""}">${g}</b>`;
  }).join("");
}

// "10/10" -> "aujourd'hui", "hier" ou "jeu. 09/10"
function dayLabel(ddmm) {
  if (!ddmm) return "";
  const [d, mo] = ddmm.split("/").map(Number);
  const now = new Date();
  const date = new Date(now.getFullYear(), mo - 1, d);
  if (date > now) date.setFullYear(now.getFullYear() - 1);
  const diff = Math.round((new Date(now.getFullYear(), now.getMonth(), now.getDate()) - date) / 86400000);
  if (diff === 0) return "aujourd'hui";
  if (diff === 1) return "hier";
  return `${date.toLocaleDateString("fr-FR", { weekday: "short" })} ${ddmm}`;
}

function matchCard(m, { showTournament = false, tournamentName = "", hideDay = false } = {}) {
  const canFollow = m.status !== "finished";
  const followedMatch = canFollow && isMatchFollowed(m);
  const rows = m.players.map((p, i) => {
    const winner = m.winner === i + 1;
    const serving = m.status === "live" && m.serving === i + 1;
    const fav = isFollowed(p.id);
    return `<div class="mc-row ${winner ? "winner" : ""}">
      <button class="star ${fav ? "on" : ""}" data-player="${p.id}" aria-pressed="${fav}" aria-label="${fav ? "Ne plus suivre" : "Suivre"} ${esc(p.name)}">${ICON.star}</button>
      <a class="who" href="#/j/${p.id}">
        <span class="pname">${esc(p.name)}${winner ? " 🏆" : ""}${serving ? '<i class="serve" title="Au service"></i>' : ""}</span>
        <span class="pclub">${esc([p.club, p.rank].filter(Boolean).join(" · "))}</span>
      </a>
      <span class="sets">${setCells(m, i)}</span>
    </div>`;
  }).join("");
  const ctx = [m.category, m.court].filter(Boolean).map(esc).join(" · ");
  return `<article class="mcard ${m.status} ${followedMatch ? "followed" : ""}" data-key="${esc(matchKey(m))}">
    <header class="mc-head">
      ${m.status === "upcoming"
        ? `<span class="mc-ctx">${ctx}</span><span class="mc-meta">${matchMeta(m, hideDay)}</span>`
        : `<a class="mc-link" href="#/m/${esc(m.tournament)}/${esc(m.ref)}" aria-label="Détails du match"><span class="mc-ctx">${ctx}</span><span class="mc-meta">${matchMeta(m, hideDay)}</span><span class="chev">${ICON.chevron}</span></a>`}
      ${canFollow ? `<button class="bell ${followedMatch ? "on" : ""}" data-match="${esc(matchKey(m))}" aria-pressed="${followedMatch}" aria-label="${followedMatch ? "Ne plus suivre ce match" : "Suivre ce match"}">${ICON.bell}</button>` : ""}
    </header>
    ${rows}
    ${m.retired ? '<div class="mc-foot muted small">Abandon</div>' : ""}
    ${showTournament ? `<a class="mc-foot small" href="#/t/${esc(m.tournament)}">${esc(cleanName(tournamentName))}</a>` : ""}
  </article>`;
}

// toutes les cartes affichées, pour retrouver un match à partir de sa clé
let shownMatches = new Map();
function remember(list) { for (const m of list) shownMatches.set(matchKey(m), m); return list; }

// ---------------------------------------------------------------------------
// Vue : accueil
// ---------------------------------------------------------------------------
let tournamentsCache = [];

function tournamentDates(t) {
  const f = (d) => new Date(d + "T12:00:00").toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
  return t.start === t.end ? f(t.start) : `${f(t.start)} → ${f(t.end)}`;
}

// "du 5 au 25 octobre 2026", "du 29 août au 3 septembre 2026", "le 20 septembre 2026"
function tournamentPeriod(t) {
  const s = new Date(t.start + "T12:00:00"), e = new Date(t.end + "T12:00:00");
  const full = (d) => d.toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" });
  if (t.start === t.end) return `le ${full(e)}`;
  const day = (d) => (d.getDate() === 1 ? "1er" : String(d.getDate()));
  const month = (d) => d.toLocaleDateString("fr-FR", { month: "long" });
  if (s.getFullYear() !== e.getFullYear()) return `du ${full(s)} au ${full(e)}`;
  if (s.getMonth() === e.getMonth()) return `du ${day(s)} au ${day(e)} ${month(e)} ${e.getFullYear()}`;
  return `du ${day(s)} ${month(s)} au ${day(e)} ${month(e)} ${e.getFullYear()}`;
}

// "57330 HETTANGE-GRANDE" -> "Hettange-Grande (57330)"
function tournamentPlace(city) {
  const m = (city || "").match(/^(\d{5})\s+(.*)$/);
  const name = (m ? m[2] : city || "").toLowerCase()
    .replace(/(^|[\s-])(\p{L})/gu, (_, sep, c) => sep + c.toUpperCase())
    .replace(/-(Les|Lès|Sur|Sous|En|Le|La|De|Du|Des|Aux)-/g, (x) => x.toLowerCase());
  return m ? `${name} (${m[1]})` : name;
}

// avancement : jour courant sur la durée, ou délai avant le début
function tournamentProgress(t) {
  const DAY = 86400000;
  const today = new Date(); today.setHours(12, 0, 0, 0);
  const s = new Date(t.start + "T12:00:00"), e = new Date(t.end + "T12:00:00");
  const total = Math.round((e - s) / DAY) + 1;
  if (today < s) {
    const n = Math.round((s - today) / DAY);
    return { pct: 0, label: n === 1 ? "Commence demain" : `Commence dans ${n} jours` };
  }
  if (today > e) return { pct: 100, label: "Terminé" };
  const n = Math.round((today - s) / DAY) + 1;
  return { pct: Math.round((n / total) * 100), label: total === 1 ? "Aujourd'hui" : `Jour ${n} sur ${total}`, live: true };
}

// état du joueur suivi sur deux lignes : étiquette + horaire/score, puis l'adversaire
function followSummary(f) {
  const s = f.status || {};
  const line = (top, opp) => `<span class="fs-top">${top}</span><span class="fs-opp"><span class="muted">contre</span> ${esc(opp)}</span>`;
  if (s.live) return line(`<span class="pill live">En direct</span><b class="score">${esc(s.live.score)}</b>`, s.live.opponent);
  if (s.next) {
    const late = s.next.at && s.next.at * 1000 < Date.now();
    const when = s.next.time ? `${cap(s.next.day)} · ${s.next.time}` : "Horaire à confirmer";
    return line(`${late ? '<span class="pill late">En attente</span>' : '<span class="pill">Prochain match</span>'}<span>${esc(when)}</span>`, s.next.opponent);
  }
  if (s.last) {
    const r = s.last.won === true ? '<span class="pill won">Victoire</span>' : s.last.won === false ? '<span class="pill lost">Défaite</span>' : '<span class="pill">Terminé</span>';
    return line(`${r}<b class="score">${esc(s.last.score)}</b>`, s.last.opponent);
  }
  return '<span class="muted">Pas de match programmé.</span>';
}

const homeView = {
  name: "home",
  async load() {
    const [ts] = await Promise.all([fetch("/api/tournaments").then((r) => r.json()), loadFollows()]);
    tournamentsCache = ts;
  },
  render() {
    const today = new Date().toISOString().slice(0, 10);
    const tlist = [...tournamentsCache].sort((a, b) => {
      const rank = (t) => (t.start <= today && today <= t.end ? 0 : t.start > today ? 1 : 2);
      return rank(a) - rank(b) || a.start.localeCompare(b.start);
    });
    const followedPlayers = follows.map((f) => `
      <a class="fitem" href="#/j/${f.id}">
        <span class="star on small-star">${ICON.star}</span>
        <span class="fbody"><b>${esc(f.name)}</b><span class="muted small">${esc([f.club, f.rank].filter(Boolean).join(" · "))}</span>
          <span class="fstatus small">${followSummary(f)}</span></span>
        <span class="chev">${ICON.chevron}</span>
      </a>`).join("");
    const followedMatches = matchFollows.filter((f) => f.match).map((f) => matchCard(f.match, { showTournament: true, tournamentName: f.tournament_name })).join("");
    remember(matchFollows.filter((f) => f.match).map((f) => f.match));
    const empty = !follows.length && !matchFollows.length;
    return `
      <section>
        <h2>Mes suivis</h2>
        ${empty ? `<p class="muted">Ouvrez un tournoi et touchez <span class="inline-icon">${ICON.star}</span> à côté d'un nom pour suivre un joueur ou une joueuse, ou <span class="inline-icon">${ICON.bell}</span> pour suivre un match.</p>` : ""}
        ${followedPlayers ? `<div class="flist">${followedPlayers}</div>` : ""}
        ${followedMatches ? `<h3 class="sub">Matchs suivis</h3><div class="mlist">${followedMatches}</div>` : ""}
      </section>
      <section>
        <h2>Tournois</h2>
        <div class="tlist">${tlist.map((t) => {
          const live = t.start <= today && today <= t.end;
          return `<a class="titem" href="#/t/${esc(t.path)}">
            ${t.logo ? `<img class="tlogo small-logo" src="${esc(t.logo)}" alt="" width="44" height="44">` : ""}
            <span class="tbody">
              <span class="eyebrow">${esc(cleanName(t.club))}</span>
              <b class="tname">${esc(cleanName(t.name))}</b>
              <span class="tline">${ICON.pin}${esc(tournamentPlace(t.city))}</span>
              <span class="tline">${ICON.cal}${esc(cap(tournamentPeriod(t)))}</span>
              <span class="tchips">${t.live ? `<span class="pill live">${t.live} en direct</span>` : ""}${t.upcoming ? `<span class="pill">${t.upcoming} à venir</span>` : ""}${!live && t.start > today ? '<span class="pill">Bientôt</span>' : ""}</span>
            </span><span class="chev">${ICON.chevron}</span></a>`;
        }).join("") || '<p class="muted">Aucun tournoi en cours.</p>'}</div>
      </section>
      <section>
        <h2>Chercher un joueur ou une joueuse</h2>
        <input id="q" class="search" type="search" placeholder="Nom" autocomplete="off" enterkeyhint="search">
        <div id="results" class="results"></div>
      </section>`;
  },
  after() {
    $("#q").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(globalSearch, 250); });
  },
};

let searchTimer = null;
async function globalSearch() {
  const q = $("#q")?.value.trim();
  const box = $("#results");
  if (!box) return;
  if (!q) { box.innerHTML = ""; return; }
  const res = await fetch(`/api/players?${new URLSearchParams({ q })}`).then((r) => r.json()).catch(() => []);
  if ($("#q")?.value.trim() !== q) return;
  box.innerHTML = res.length ? res.map((p) => `
    <div class="result">
      <button class="star ${isFollowed(p.id) ? "on" : ""}" data-player="${p.id}" aria-pressed="${isFollowed(p.id)}" aria-label="Suivre ${esc(p.name)}">${ICON.star}</button>
      <a class="who" href="#/j/${p.id}"><span class="pname">${esc(p.name)}</span><span class="pclub">${esc([p.club, p.rank].filter(Boolean).join(" · "))}</span></a>
      <span class="chev">${ICON.chevron}</span>
    </div>`).join("") : '<div class="empty muted">Aucun résultat dans les tournois en cours ou à venir.</div>';
  for (const p of res) playersSeen.set(p.id, p);
}

// ---------------------------------------------------------------------------
// Vue : tournoi
// ---------------------------------------------------------------------------
const TABS = [["live", "En direct"], ["upcoming", "À venir"], ["finished", "Terminés"], ["infos", "Infos"]];

// "YVON RADERMECKER" -> "Yvon Radermecker"
const titleName = (s) => (s || "").toLowerCase().replace(/(^|[\s-])(\p{L})/gu, (_, sep, c) => sep + c.toUpperCase());

function infosTab(t, info) {
  const w = info.weather;
  const weather = w && w.periods?.length ? `
    <section class="card info-card">
      <div class="info-head"><h2>Météo du jour</h2><span class="muted small">${esc(titleName(w.place))}</span><b class="wnow">${esc(w.current)}</b></div>
      <div class="wrow">${w.periods.map((p) => `
        <div class="wcell"><span class="muted small">${esc(p.label)}</span>
          <span class="wtemp"><span class="wicon" aria-hidden="true">${esc(p.icon)}</span><b>${esc(p.temp)}</b></span>
          <span class="small">${esc(p.desc)}</span>
          <span class="muted small">${p.details.map(esc).join("<br>")}</span></div>`).join("")}</div>
    </section>` : "";
  const figures = info.figures?.length ? `
    <section class="card info-card">
      <h2>Quelques chiffres</h2>
      <div class="figs">${info.figures.map((f) => `<div class="fig"><b>${esc(f.value)}</b><span class="muted small">${esc(f.label)}</span></div>`).join("")}</div>
      ${(info.shares || []).map((x) => `
        <div class="share"><span>${esc(x.label)} <span class="muted">(${esc(x.count)})</span></span><b>${esc(x.pct)}</b>
          <span class="bar"><span style="width:${parseFloat(String(x.pct).replace(",", ".")) || 0}%"></span></span></div>`).join("")}
    </section>` : "";
  const poster = info.poster ? `
    <section class="card info-card poster-card">
      <h2>Affiche</h2>
      <button id="poster" class="poster-btn" aria-label="Agrandir l'affiche"><img src="${esc(info.poster)}" alt="Affiche du tournoi" loading="lazy"></button>
      <dialog id="poster-dialog" class="poster-dialog"><img src="${esc(info.poster)}" alt="Affiche du tournoi"><p class="muted small">Touchez pour fermer</p></dialog>
    </section>` : "";
  return (weather + figures + poster) || '<p class="muted empty-tab">Pas d\'informations supplémentaires pour ce tournoi.</p>';
}

function groupBy(list, keyFn) {
  const out = [];
  for (const m of list) {
    const k = keyFn(m);
    if (!out.length || out[out.length - 1][0] !== k) out.push([k, []]);
    out[out.length - 1][1].push(m);
  }
  return out;
}

function tournamentView(path) {
  let data = null;
  let tab = sessionTab(path);
  let filter = "";
  return {
    name: "tournament",
    async load() {
      const [d] = await Promise.all([fetch(`/api/t/${path}`).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); }), loadFollows()]);
      data = d;
      if (!tab) tab = data.live.length ? "live" : data.upcoming.length ? "upcoming" : "finished";
    },
    render() {
      const t = data.tournament;
      const info = t.info || {};
      if (tab === "infos") return this.header(t, info) + infosTab(t, info);
      const all = data[tab] || [];
      for (const k of ["live", "upcoming", "finished"]) remember(data[k]);
      for (const k of ["live", "upcoming", "finished"]) for (const m of data[k]) for (const p of m.players) playersSeen.set(p.id, p);
      const f = fold(filter.trim());
      const list = f ? all.filter((m) => m.players.some((p) => fold(p.name).includes(f))) : all;
      let body;
      if (!list.length) {
        body = `<p class="muted empty-tab">${f ? "Aucun match pour ce nom." : tab === "live" ? "Aucun match en cours pour l'instant." : tab === "upcoming" ? "Aucun match programmé." : "Aucun match terminé."}</p>`;
      } else if (tab === "upcoming") {
        body = groupBy(list, (m) => (m.day ? cap(m.day) : "Horaire à confirmer")).map(([d, ms]) => `<h3 class="day">${esc(d)}</h3><div class="mlist">${ms.map((m) => matchCard(m, { hideDay: true })).join("")}</div>`).join("");
      } else if (tab === "finished") {
        body = groupBy(list, (m) => m.date || "").map(([d, ms]) => `${d ? `<h3 class="day">${esc(cap(dayLabel(d)))}</h3>` : ""}<div class="mlist">${ms.map((m) => matchCard(m, { hideDay: true })).join("")}</div>`).join("");
      } else {
        body = `<div class="mlist">${list.map((m) => matchCard(m)).join("")}</div>`;
      }
      const upd = data.updated ? new Date(data.updated * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
      return this.header(t, info) + `
        <input id="tfilter" class="search" type="search" placeholder="Filtrer par nom" value="${esc(filter)}" autocomplete="off">
        ${body}
        <p class="updated muted small">${tab === "live" ? `Actualisé automatiquement${upd ? ` · ${upd}` : ""}` : ""}</p>`;
    },
    header(t, info) {
      const tabsHtml = () => `<nav class="tabs" role="tablist">${TABS.map(([k, l]) => `<button role="tab" data-tab="${k}" aria-selected="${k === tab}">${l}${k === "infos" ? "" : ` <span class="count">${data[k].length}</span>`}</button>`).join("")}</nav>`;
      return `
        <header class="thead card">
          <div class="thead-top">
            ${info.logo ? `<img class="tlogo" src="${esc(info.logo)}" alt="" width="56" height="56">` : ""}
            <div>
              <p class="eyebrow">${esc(cleanName(t.club))}</p>
              <h1>${esc(cleanName(t.name))}${info.edition ? ` <span class="edition">${esc(info.edition)}</span>` : ""}</h1>
            </div>
          </div>
          <ul class="tfacts">
            <li>${ICON.pin}<span>${esc(tournamentPlace(t.city))}</span></li>
            <li>${ICON.cal}<span>${esc(cap(tournamentPeriod(t)))}</span></li>
            ${info.surface ? `<li>${ICON.court}<span>${esc(info.surface)}</span></li>` : ""}
            ${info.prize ? `<li>${ICON.euro}<span>Dotation ${esc(info.prize)}</span></li>` : ""}
            ${info.referee ? `<li>${ICON.whistle}<span>Juge-arbitre : ${esc(titleName(info.referee.name))}
              ${info.referee.phone ? `<a class="tel" href="tel:${esc(info.referee.phone)}">${ICON.phone}Appeler</a>` : ""}</span></li>` : ""}
          </ul>
          ${(() => {
            const p = tournamentProgress(t);
            return `<div class="tprogress ${p.live ? "live" : ""}" role="progressbar" aria-valuenow="${p.pct}" aria-valuemin="0" aria-valuemax="100" aria-label="Avancement du tournoi">
              <span class="bar"><span style="width:${p.pct}%"></span></span><span class="small">${esc(p.label)}</span></div>`;
          })()}
        </header>
        ${tabsHtml()}`;
    },
    after() {
      for (const b of view().querySelectorAll("[data-tab]")) {
        b.addEventListener("click", () => { tab = b.dataset.tab; sessionTab(path, tab); rerender(); });
      }
      $("#poster")?.addEventListener("click", () => $("#poster-dialog").showModal());
      $("#poster-dialog")?.addEventListener("click", (e) => e.currentTarget.close());
      const input = $("#tfilter");
      if (!input) return;
      input.addEventListener("input", () => {
        filter = input.value;
        const pos = input.selectionStart;
        rerender();
        const again = $("#tfilter");
        again.focus();
        again.setSelectionRange(pos, pos);
      });
    },
  };
}

function sessionTab(path, value) {
  try {
    if (value) sessionStorage.setItem(`tab:${path}`, value);
    return sessionStorage.getItem(`tab:${path}`);
  } catch { return null; }
}

// ---------------------------------------------------------------------------
// Vue : joueur
// ---------------------------------------------------------------------------
const playersSeen = new Map();

function settingsHtml(level, reminder, scope) {
  return `<div class="settings" data-scope="${scope}">
    <div class="seg" role="radiogroup" aria-label="Notifications">
      <button role="radio" data-level="match" aria-checked="${level === "match"}">Résultat</button>
      <button role="radio" data-level="set" aria-checked="${level === "set"}">Set par set</button>
    </div>
    <p class="muted small">${LEVEL_HELP[level]}</p>
    <label class="reminder small">Notification début de match
      <select>${REMINDERS.map(([v, l]) => `<option value="${v}" ${String(reminder ?? "") === v ? "selected" : ""}>${l}</option>`).join("")}</select>
    </label>
  </div>`;
}

function playerView(id) {
  let data = null;
  return {
    name: "player",
    async load() {
      const [d] = await Promise.all([fetch(`/api/p/${id}`).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); }), loadFollows()]);
      data = d;
      playersSeen.set(d.id, d);
    },
    render() {
      const f = follows.find((x) => x.id === data.id);
      let wins = 0, losses = 0;
      const blocks = data.history.map((h) => {
        const ms = [...h.live, ...h.upcoming, ...h.finished];
        remember(ms);
        for (const m of h.finished) {
          const side = m.players[0].id === data.id ? 1 : 2;
          if (m.winner === side) wins++; else if (m.winner) losses++;
        }
        return `<section class="hist">
          <a class="hist-t" href="#/t/${esc(h.tournament.path)}"><b>${esc(cleanName(h.tournament.name))}</b><span class="muted small">${esc(cleanName(h.tournament.club))} · ${esc(tournamentDates(h.tournament))}</span></a>
          ${ms.length ? `<div class="mlist">${ms.map((m) => matchCard(m)).join("")}</div>` : '<p class="muted small">Aucun match joué ou programmé pour l\'instant.</p>'}
        </section>`;
      }).join("");
      return `
        <div class="vhead">
          <h1>${esc(data.name)}</h1>
          <p class="muted small">${esc([data.club, data.rank].filter(Boolean).join(" · "))}${wins + losses ? ` · ${wins} victoire${wins > 1 ? "s" : ""}, ${losses} défaite${losses > 1 ? "s" : ""}` : ""}</p>
        </div>
        <div class="card follow-box">
          <button class="btn ${f ? "secondary" : ""} follow-btn" data-player="${data.id}">${ICON.star}<span>${f ? "Suivi · ne plus suivre" : "Suivre et être notifié"}</span></button>
          ${f ? settingsHtml(f.level, f.reminder, "player") : ""}
        </div>
        <h2>Historique</h2>
        ${blocks || '<p class="muted">Aucun match trouvé.</p>'}`;
    },
    after() {
      bindSettings(view().querySelector('.settings[data-scope="player"]'), (patch) =>
        savePlayers(follows.map((x) => (x.id === data.id ? { ...x, ...patch } : x))));
    },
  };
}

function bindSettings(box, save) {
  if (!box) return;
  for (const b of box.querySelectorAll("[data-level]")) b.addEventListener("click", () => save({ level: b.dataset.level }));
  const sel = $("select", box);
  sel.addEventListener("change", () => save({ reminder: sel.value === "" ? null : +sel.value }));
}

// ---------------------------------------------------------------------------
// Vue : match (résumé, services gagnés, jeu par jeu)
// ---------------------------------------------------------------------------
function matchView(path) {
  let data = null;
  let svcScope = "total";
  let gameSet = null;
  return {
    name: "match",
    async load() {
      const [d] = await Promise.all([fetch(`/api/m/${path}`).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); }), loadFollows()]);
      data = d;
      remember([d.match]);
      for (const p of d.match.players) playersSeen.set(p.id, p);
    },
    render() {
      const m = data.match, st = data.stats || {};
      const [a, b] = m.players;
      const setsWon = [0, 1].map((i) => m.players[i].games.filter((g, k) => !(m.live_set && k === m.players[i].games.length - 1) && g > (m.players[1 - i].games[k] ?? 0)).length);
      const followed = m.status !== "finished" && isMatchFollowed(m);
      const nSets = Math.max(a.games.length, b.games.length);
      const scoreRows = m.players.map((p, i) => `
        <div class="sb-row ${m.winner === i + 1 ? "winner" : ""}">
          <button class="star ${isFollowed(p.id) ? "on" : ""}" data-player="${p.id}" aria-pressed="${isFollowed(p.id)}" aria-label="Suivre ${esc(p.name)}">${ICON.star}</button>
          <a class="who" href="#/j/${p.id}"><span class="pname">${esc(p.name)}${m.winner === i + 1 ? " 🏆" : ""}${m.status === "live" && m.serving === i + 1 ? '<i class="serve" title="Au service"></i>' : ""}</span>
            <span class="pclub">${esc([p.club, p.rank].filter(Boolean).join(" · "))}</span></a>
          <b class="sb-sets">${setsWon[i]}</b>
          <span class="sets">${setCells(m, i)}</span>
        </div>`).join("");
      const durations = st.durations?.total ? `
        <div class="durations">
          <span><b>${esc(st.durations.total)}</b><small>Match</small></span>
          ${st.durations.sets.map((d, i) => `<span><b>${esc(d)}</b><small>Set ${i + 1}</small></span>`).join("")}
        </div>` : "";

      // services gagnés
      // les clés numériques passent avant "total" dans un objet JS : on impose l'ordre
      const scopes = Object.keys(st.services || {}).sort((x, y) => (x === "total" ? -1 : y === "total" ? 1 : x - y));
      if (!scopes.includes(svcScope)) svcScope = scopes[0] || "total";
      const svc = st.services?.[svcScope];
      const services = svc ? `
        <section class="card info-card">
          <h2>Jeux de service gagnés</h2>
          ${scopes.length > 1 ? `<div class="seg pills" data-group="svc">${scopes.map((k) => `<button data-v="${esc(k)}" aria-checked="${k === svcScope}">${k === "total" ? "Match" : `Set ${esc(k)}`}</button>`).join("")}</div>` : ""}
          ${svc.map((x, i) => `
            <div class="svc">
              <div class="svc-head"><span>${esc(m.players[i].name)}</span><b>${x.won} / ${x.services}</b></div>
              <span class="bar"><span style="width:${x.services ? Math.round((x.won / x.services) * 100) : 0}%"></span></span>
              <span class="muted small">${x.services ? Math.round((x.won / x.services) * 100) : 0} % de jeux de service remportés</span>
            </div>`).join("")}
        </section>` : "";

      // jeu par jeu
      const games = st.games || [];
      const setsAvail = [...new Set(games.map((g) => g.set))];
      if (!setsAvail.includes(gameSet)) gameSet = setsAvail[setsAvail.length - 1] ?? null;
      const gp = games.filter((g) => g.set === gameSet);
      const breaks = [0, 1].map((i) => games.filter((g) => g.break && g.server === 2 - i).length);
      const gameByGame = games.length ? `
        <section class="card info-card">
          <h2>Jeu par jeu</h2>
          <p class="muted small">Breaks : ${esc(a.name)} ${breaks[0]} · ${esc(b.name)} ${breaks[1]}</p>
          ${setsAvail.length > 1 ? `<div class="seg pills" data-group="set">${setsAvail.map((k) => `<button data-v="${k}" aria-checked="${k === gameSet}">Set ${k}</button>`).join("")}</div>` : ""}
          <div class="gp-head"><span>${esc(a.name)}</span><span></span><span>${esc(b.name)}</span></div>
          <ol class="gp">${gp.map((g, k) => `
            <li class="${g.break ? "brk" : ""}">
              <span class="side">${g.server === 1 ? `<i class="ball-ico ${g.break ? "lost" : ""}" title="Au service"></i>` : ""}${g.break && g.server === 2 ? '<span class="tag">Break</span>' : ""}</span>
              <span class="gscore"><small>${k + 1}</small>${g.score[0]} – ${g.score[1]}</span>
              <span class="side">${g.server === 2 ? `<i class="ball-ico ${g.break ? "lost" : ""}" title="Au service"></i>` : ""}${g.break && g.server === 1 ? '<span class="tag">Break</span>' : ""}</span>
            </li>`).join("")}</ol>
        </section>` : "";

      const when = st.datetime ? cap(st.datetime) : m.date ? cap(dayLabel(m.date)) : "";
      return `
        <header class="card mhead ${m.status}">
          <div class="mhead-top">
            <span class="eyebrow">${esc(st.category || m.category || "")}</span>
            ${m.status === "live" ? '<span class="pill live">En direct</span>' : '<span class="pill">Terminé</span>'}
            ${m.status !== "finished" ? `<button class="bell ${followed ? "on" : ""}" data-match="${esc(matchKey(m))}" aria-pressed="${followed}" aria-label="${followed ? "Ne plus suivre ce match" : "Suivre ce match"}">${ICON.bell}</button>` : ""}
          </div>
          <p class="muted small">${esc([when, st.court || m.court].filter(Boolean).join(" · "))}</p>
          <div class="sb">${scoreRows}</div>
          ${m.retired ? '<p class="muted small">Abandon</p>' : ""}
          ${durations}
          <a class="mc-foot small" href="#/t/${esc(data.tournament.path)}">${esc(cleanName(data.tournament.name))}</a>
        </header>
        ${services}
        ${gameByGame}
        ${!data.stats ? '<p class="muted small empty-tab">Statistiques indisponibles pour ce match.</p>' : ""}
        ${m.status === "live" ? '<p class="updated muted small">Actualisé automatiquement</p>' : ""}`;
    },
    after() {
      for (const b of view().querySelectorAll('[data-group="svc"] button')) b.addEventListener("click", () => { svcScope = b.dataset.v; rerender(); });
      for (const b of view().querySelectorAll('[data-group="set"] button')) b.addEventListener("click", () => { gameSet = +b.dataset.v; rerender(); });
    },
  };
}

// ---------------------------------------------------------------------------
// Routage et actualisation
// ---------------------------------------------------------------------------
function go(hash) { location.hash = hash; }

function route() {
  const h = location.hash.replace(/^#/, "") || "/";
  if (h.startsWith("/t/")) return tournamentView(decodeURIComponent(h.slice(3)));
  if (h.startsWith("/j/")) return playerView(+h.slice(3));
  if (h.startsWith("/m/")) return matchView(decodeURIComponent(h.slice(3)));
  return homeView;
}

// barre du haut : nom de l'appli à l'accueil, bouton retour ailleurs
function updateNav(v) {
  const home = v.name === "home";
  $("#nav-back").hidden = home;
  $(".brand-name").hidden = !home;
  $("#nav-back span").textContent = v.name === "tournament" ? "Accueil" : "Retour";
  $("#nav-back").toggleAttribute("data-back", v.name !== "tournament");
}

function rerender() {
  if (!current) return;
  const y = scrollY;
  view().innerHTML = current.render();
  current.after?.();
  scrollTo(0, y);
}

async function show() {
  const v = route();
  current = v;
  updateNav(v);
  view().setAttribute("aria-busy", "true");
  try {
    await v.load();
    if (current !== v) return; // navigation entre-temps
    view().innerHTML = v.render();
    v.after?.();
    scrollTo(0, 0);
  } catch (e) {
    console.error(e);
    view().innerHTML = '<p class="muted empty-tab">Page introuvable.</p>';
  }
  view().removeAttribute("aria-busy");
}

async function refresh() {
  if (!current || document.visibilityState !== "visible") return;
  // ne pas perturber une saisie en cours
  if (document.activeElement?.matches("input, select")) return;
  try { await current.load(); rerender(); } catch (e) { console.error(e); }
}
window.onPushData = refresh;

// « Retour » revient à la vue précédente du site, sinon à l'accueil
let navCount = 0;
window.addEventListener("hashchange", () => { navCount++; });

// clics délégués : retour, étoiles (joueurs) et cloches (matchs)
document.addEventListener("click", (e) => {
  if (e.target.closest("[data-back]") && navCount > 0) {
    e.preventDefault();
    history.back();
    return;
  }
  const star = e.target.closest("[data-player]");
  if (star) {
    e.preventDefault();
    const id = +star.dataset.player;
    const p = follows.find((f) => f.id === id) || playersSeen.get(id) || [...shownMatches.values()].flatMap((m) => m.players).find((x) => x.id === id);
    if (p) togglePlayer(p);
    return;
  }
  const bell = e.target.closest("[data-match]");
  if (bell) {
    e.preventDefault();
    const m = shownMatches.get(bell.dataset.match);
    if (m) toggleMatch(m);
  }
});

// ---------------------------------------------------------------------------
// Partage vers le téléphone / l'écran d'accueil iOS
// ---------------------------------------------------------------------------
const LEVEL_CODE = { match: "m", set: "s" };
const CODE_LEVEL = { m: "match", s: "set", j: "set" };

function syncShareState() {
  const code = follows.map((f) => `${f.id}.${LEVEL_CODE[f.level]}.${f.reminder ?? "-"}`).join(",");
  const start = code ? `/?suivre=${code}` : "/";
  // l'icône ajoutée à l'écran d'accueil (iOS) reprendra ces suivis
  $("#manifest").href = `/manifest.webmanifest?start=${encodeURIComponent(start)}`;
  if (env.ios && !env.standalone) history.replaceState(null, "", start + location.hash);
  const qr = $("#qr");
  if (!env.mobile) {
    $("#qr-img").src = `/qr.svg?data=${encodeURIComponent(location.origin + start)}`;
    qr.hidden = false;
  } else {
    qr.hidden = true;
  }
}

async function importFromUrl() {
  const raw = new URLSearchParams(location.search).get("suivre");
  if (!raw) return;
  const wanted = raw.split(",").map((s) => s.split(".")).filter((p) => /^\d+$/.test(p[0]));
  const add = wanted.filter((p) => !isFollowed(+p[0]));
  if (add.length) {
    const players = await fetch(`/api/players?ids=${add.map((p) => p[0]).join(",")}`).then((r) => r.json());
    const byId = Object.fromEntries(players.map((p) => [p.id, p]));
    const extra = add.filter((p) => byId[+p[0]]).map((p) => ({
      ...byId[+p[0]], level: CODE_LEVEL[p[1]] || "match", reminder: p[2] && p[2] !== "-" ? +p[2] : null,
    }));
    if (extra.length) await savePlayers([...follows, ...extra]);
  }
  if (!(env.ios && !env.standalone)) history.replaceState(null, "", "/" + location.hash);
}

// ---------------------------------------------------------------------------
async function main() {
  renderNotif("loading");
  config = await fetch("/api/config").then((r) => r.json());
  await loadFollows();
  await importFromUrl();
  // ancien lien de notification (?joueur=ID)
  const legacy = new URLSearchParams(location.search).get("joueur");
  if (legacy && /^\d+$/.test(legacy)) { history.replaceState(null, "", "/"); location.hash = `#/j/${legacy}`; }
  window.addEventListener("hashchange", show);
  $("#footer-test").addEventListener("click", sendTest);
  await show();
  try { await checkNotifications(); } catch (e) { console.error(e); renderNotif("error", esc(e.message || e)); }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    refresh();
    if (!["testing", "test-received", "test-hidden"].includes(notifState)) checkNotifications().catch(console.error);
  });
  clearInterval(refreshTimer);
  refreshTimer = setInterval(refresh, REFRESH_MS);
}
main();
