"use strict";

// ---------------------------------------------------------------------------
// Environnement
// ---------------------------------------------------------------------------
const ua = navigator.userAgent;
const env = {
  ios: /iPad|iPhone|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1),
  android: /Android/i.test(ua),
  standalone: matchMedia("(display-mode: standalone)").matches || navigator.standalone === true,
  // navigateurs intégrés (Instagram, Facebook, appli Google…) : pas de notifications web
  inApp: /FBAN|FBAV|Instagram|Line\/|Snapchat|LinkedInApp|GSA\/|; wv\)/.test(ua),
  iosVersion: (() => { const m = ua.match(/OS (\d+)_(\d+)/); return m ? +m[1] + +m[2] / 100 : null; })(),
  pushApi: "serviceWorker" in navigator && "PushManager" in window && "Notification" in window,
  firefox: /Firefox|FxiOS/.test(ua),
  samsung: /SamsungBrowser/.test(ua),
  edge: /Edg\//.test(ua),
  mac: /Macintosh/.test(ua),
  windows: /Windows/.test(ua),
};
// "version ordinateur" des navigateurs mobiles : l'UA ment, mais pas le tactile sans survol
env.mobile = env.ios || env.android || /Mobi/i.test(ua) || navigator.userAgentData?.mobile === true ||
  (matchMedia("(pointer: coarse)").matches && matchMedia("(hover: none)").matches);

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* navigation privée */ } },
};

// ---------------------------------------------------------------------------
// Identité anonyme de l'appareil
// ---------------------------------------------------------------------------
function randomHex(n) {
  return [...crypto.getRandomValues(new Uint8Array(n))].map((b) => b.toString(16).padStart(2, "0")).join("");
}
let creds = null;
try { creds = JSON.parse(store.get("device") || "null"); } catch { creds = null; }
if (!creds || !creds.id || !creds.secret) {
  creds = { id: randomHex(16), secret: randomHex(32) };
  store.set("device", JSON.stringify(creds));
}
// le service worker n'a pas accès au localStorage : il lit les identifiants dans le cache
if ("caches" in window) {
  caches.open("cfg").then((c) => c.put("/__device", new Response(JSON.stringify(creds)))).catch(() => {});
}

async function api(path, opts = {}) {
  const r = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json", "X-Device-Id": creds.id, "X-Device-Secret": creds.secret, ...(opts.headers || {}) },
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

// ---------------------------------------------------------------------------
// Notifications : détection fine de l'état et guidage
// ---------------------------------------------------------------------------
const ICON_SHARE = '<span class="kbd" aria-label="Partager"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12M7 8l5-5 5 5"/><path d="M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7"/></svg></span>';
const ICON_ADD = '<span class="kbd" aria-label="Ajouter"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="4"/><path d="M12 8v8M8 12h8"/></svg></span>';
const ICON_DOTS = '<span class="kbd">⋮</span>';

let config = null;
let swReg = null;
let notifState = null;
let serverDevice = null;
let testTimer = null;

function blockedHelp() {
  if (env.ios) {
    return `<ol><li>Ouvrez <b>Réglages</b> sur l'iPhone, puis <b>Notifications</b>.</li>
      <li>Touchez <b>${esc(config?.appName || "l'application")}</b> et activez <b>Autoriser les notifications</b>.</li>
      <li>Revenez ici.</li></ol>`;
  }
  if (env.android && env.standalone) {
    return `<ol><li>Appui long sur l'icône de l'application, puis <b>Infos sur l'appli</b> (ⓘ).</li>
      <li><b>Notifications</b> → activez-les.</li><li>Revenez ici.</li></ol>`;
  }
  if (env.android) {
    return `<ol><li>Touchez l'icône à gauche de l'adresse (réglages du site) ou ${ICON_DOTS} → <b>Paramètres</b> → <b>Paramètres des sites</b>.</li>
      <li><b>Notifications</b> → <b>Autoriser</b>.</li>
      <li>Si Chrome lui-même est bloqué : Paramètres du téléphone → <b>Applications</b> → <b>${env.samsung ? "Samsung Internet" : "Chrome"}</b> → <b>Notifications</b>.</li></ol>`;
  }
  if (env.firefox) return `<ol><li>Cliquez sur l'icône à gauche de l'adresse.</li><li>Supprimez le blocage des notifications puis rechargez la page.</li></ol>`;
  if (env.mac && !/Chrome/.test(ua)) return `<ol><li>Safari → <b>Réglages</b> → <b>Sites web</b> → <b>Notifications</b>.</li><li>Choisissez <b>Autoriser</b> pour ce site.</li></ol>`;
  return `<ol><li>Cliquez sur l'icône à gauche de l'adresse (cadenas ou réglages).</li><li><b>Notifications</b> → <b>Autoriser</b>, puis rechargez la page.</li></ol>`;
}

function systemHelp() {
  if (env.ios) return `<ul><li><b>Réglages → Notifications → ${esc(config?.appName || "l'application")}</b> : autorisées, avec l'affichage sur l'écran verrouillé.</li><li>Un mode <b>Concentration</b> (Ne pas déranger, Sommeil…) peut les masquer.</li></ul>`;
  if (env.android) return `<ul><li>Paramètres du téléphone → <b>Applications</b> → <b>${env.samsung ? "Samsung Internet" : "Chrome"}</b> → <b>Notifications</b> : tout doit être autorisé, y compris la catégorie des sites.</li><li>Mode <b>Ne pas déranger</b> ou <b>économie d'énergie</b> : ils retardent ou masquent les notifications.</li></ul>`;
  if (env.windows) return `<ul><li>Paramètres Windows → <b>Système</b> → <b>Notifications</b> : activez-les pour ${env.edge ? "Edge" : "votre navigateur"}.</li><li>Désactivez <b>Ne pas déranger</b> / l'assistant de concentration.</li></ul>`;
  if (env.mac) return `<ul><li>Réglages Système → <b>Notifications</b> → votre navigateur : autoriser.</li><li>Vérifiez qu'aucun mode Concentration n'est actif.</li></ul>`;
  return `<ul><li>Vérifiez que les notifications du navigateur sont autorisées dans les réglages du système.</li></ul>`;
}

function renderNotif(state, extra = "") {
  notifState = state;
  const el = $("#notif");
  const views = {
    loading: ["", "Vérification des notifications…", ""],
    inapp: ["bad", "Ouvrez cette page dans votre navigateur",
      `<p>Vous êtes dans le navigateur d'une application (Facebook, Instagram, Google…), qui ne peut pas recevoir de notifications.</p>
       <p>Touchez ${ICON_DOTS} ou ${ICON_SHARE} puis <b>Ouvrir dans ${env.ios ? "Safari" : "le navigateur"}</b>.</p>`],
    "ios-old": ["bad", "iOS trop ancien",
      `<p>Les notifications web demandent iOS 16.4 ou plus récent. Mettez l'iPhone à jour dans Réglages → Général → Mise à jour logicielle.</p>`],
    "ios-install": ["warn", "Une étape de plus sur iPhone",
      `<p>Sur iPhone, les notifications ne fonctionnent qu'une fois la page ajoutée à l'écran d'accueil :</p>
       <ol><li>Touchez ${ICON_SHARE} <b>Partager</b>${/CriOS|EdgiOS/.test(ua) ? " (en haut à droite)" : " en bas de Safari"}.</li>
       <li>Choisissez ${ICON_ADD} <b>Sur l'écran d'accueil</b>, puis <b>Ajouter</b>.</li>
       <li>Ouvrez <b>${esc(config?.appName || "l'icône")}</b> depuis l'écran d'accueil et activez les notifications.</li></ol>
       <p class="muted small">Votre sélection sera reprise automatiquement.</p>`],
    unsupported: ["bad", "Navigateur non compatible",
      `<p>Ce navigateur ne gère pas les notifications web. Utilisez Chrome, Edge, Firefox, Samsung Internet ou Safari récent.</p>`],
    ask: ["warn", "Notifications désactivées",
      `<p>Activez-les pour suivre les matchs de vos joueurs et joueuses.</p>
       <div class="actions"><button class="btn" id="btn-enable">Activer les notifications</button></div>`],
    denied: ["bad", "Notifications bloquées",
      `<p>Les notifications ont été refusées pour ce site. Pour les réactiver :</p>${blockedHelp()}
       <div class="actions"><button class="btn secondary" id="btn-recheck">J'ai changé le réglage</button></div>`],
    error: ["bad", "Impossible d'activer les notifications", `<p>${extra}</p>
       <div class="actions"><button class="btn" id="btn-enable">Réessayer</button></div>`],
    ok: ["ok compact", "Notifications actives",
      `<button class="btn secondary small-btn" id="btn-test">Tester</button>`],
    testing: ["ok", "Notification de test envoyée…", `<p>Elle devrait apparaître dans quelques secondes.</p>`],
    "test-received": ["ok", "Avez-vous vu la notification ?",
      `<p>L'appareil l'a bien reçue. Si elle ne s'est pas affichée, c'est un réglage du téléphone ou de l'ordinateur qui la masque.</p>
       <div class="actions"><button class="btn" id="btn-seen">Oui, je l'ai vue</button><button class="btn secondary" id="btn-notseen">Non</button></div>`],
    "test-hidden": ["bad", "Notifications masquées par le système",
      `<p>Le site est autorisé, mais le système n'affiche pas les notifications. À vérifier :</p>${systemHelp()}
       <div class="actions"><button class="btn secondary" id="btn-test">Refaire un test</button></div>`],
    "test-lost": ["bad", "La notification n'est pas arrivée",
      `${extra ? `<p class="small muted">${esc(extra)}</p>` : ""}
       <p>Le service de notifications n'a pas pu joindre cet appareil.</p>
       <ul><li>Vérifiez la connexion internet.</li>
       ${/Brave/.test(ua) || navigator.brave ? "<li>Brave : activez <b>Utiliser les services Google pour la messagerie push</b> dans brave://settings/privacy.</li>" : ""}
       ${env.android ? "<li>Désactivez l'économie d'énergie pour le navigateur.</li>" : ""}</ul>
       <div class="actions"><button class="btn" id="btn-resub">Réactiver</button><button class="btn secondary" id="btn-test">Refaire un test</button></div>`],
  };
  const [cls, title, body] = views[state];
  // une fois l'affichage confirmé, plus rien à montrer : juste un lien discret en pied de page
  const quiet = state === "loading" || (state === "ok" && store.get("seen-ok"));
  el.hidden = quiet;
  $("#footer-test").hidden = !(state === "ok" && store.get("seen-ok"));
  if (quiet) return;
  el.className = `card notif ${cls}`;
  el.innerHTML = `<h3>${cls ? '<span class="dot"></span>' : ""}${title}</h3>${body}`;
  $("#btn-enable", el)?.addEventListener("click", enableNotifications);
  $("#btn-recheck", el)?.addEventListener("click", () => location.reload());
  $("#btn-test", el)?.addEventListener("click", sendTest);
  $("#btn-resub", el)?.addEventListener("click", () => subscribe(true));
  $("#btn-seen", el)?.addEventListener("click", () => { store.set("seen-ok", "1"); renderNotif("ok"); });
  $("#btn-notseen", el)?.addEventListener("click", () => renderNotif("test-hidden"));
}

function urlB64ToUint8Array(b64) {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

async function subscribe(force = false) {
  try {
    let sub = await swReg.pushManager.getSubscription();
    if (sub && force) { await sub.unsubscribe(); sub = null; }
    if (!sub) {
      sub = await swReg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlB64ToUint8Array(config.vapidPublicKey) });
    }
    serverDevice = await api("/api/device", { method: "PUT", body: { subscription: sub.toJSON() } });
    return true;
  } catch (e) {
    console.error(e);
    if (Notification.permission === "denied") { renderNotif("denied"); return false; }
    renderNotif("error", /Brave/.test(ua) || navigator.brave
      ? "Brave bloque le service de notifications : activez <b>Utiliser les services Google pour la messagerie push</b> dans brave://settings/privacy, puis réessayez."
      : `Le navigateur a refusé l'abonnement (${esc(e.message || e)}).`);
    return false;
  }
}

async function enableNotifications() {
  const btn = $("#btn-enable");
  if (btn) btn.disabled = true;
  let perm;
  try { perm = await Notification.requestPermission(); } catch { perm = Notification.permission; }
  if (perm === "denied") return renderNotif("denied");
  if (perm !== "granted") return renderNotif("ask"); // fenêtre fermée sans répondre
  if (await subscribe()) await sendTest();
}

async function sendTest() {
  renderNotif("testing");
  clearTimeout(testTimer);
  try {
    const r = await api("/api/test", { method: "POST" });
    if (!r.sent) return renderNotif("test-lost", r.error === "expired" ? "Abonnement expiré" : r.error || "");
    pendingNonce = r.nonce;
    testTimer = setTimeout(() => { if (notifState === "testing") renderNotif("test-lost"); }, 20000);
  } catch (e) {
    renderNotif("test-lost", String(e.message || e));
  }
}

let pendingNonce = null;
navigator.serviceWorker?.addEventListener("message", (e) => {
  if (e.data?.type === "push" && e.data.nonce && e.data.nonce === pendingNonce) {
    clearTimeout(testTimer);
    pendingNonce = null;
    renderNotif("test-received");
  }
  if (e.data?.type === "push" && e.data.kind !== "test") window.onPushData?.();
});

async function checkNotifications() {
  if (env.inApp) return renderNotif("inapp");
  if (env.ios && env.iosVersion && env.iosVersion < 16.4) return renderNotif("ios-old");
  if (env.ios && !env.standalone) return renderNotif("ios-install");
  if (!env.pushApi) return renderNotif(env.ios ? "ios-install" : "unsupported");
  if (!swReg) swReg = await navigator.serviceWorker.register("/sw", { scope: "/" });
  await navigator.serviceWorker.ready;
  // permissionState reflète aussi un blocage global du navigateur
  let perm = Notification.permission;
  try { perm = await swReg.pushManager.permissionState({ userVisibleOnly: true, applicationServerKey: urlB64ToUint8Array(config.vapidPublicKey) }); } catch { /* ancien navigateur */ }
  if (perm === "denied") return renderNotif("denied");
  if (perm !== "granted") return renderNotif("ask");
  // autorisé : vérifier que l'abonnement existe encore des deux côtés
  serverDevice = await api("/api/device");
  const sub = await swReg.pushManager.getSubscription();
  if (!sub || !serverDevice.subscribed) {
    if (!(await subscribe())) return;
  } else if (serverDevice.push_error && serverDevice.push_error !== "expired") {
    return renderNotif("test-lost", serverDevice.push_error);
  }
  renderNotif("ok", store.get("seen-ok") ? "" : '<p class="small muted">Faites un test pour vérifier que les notifications s\'affichent bien.</p>');
}

