"use strict";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data.json(); } catch { data = { title: "Tennis", body: event.data?.text() || "" }; }
  event.waitUntil((async () => {
    await self.registration.showNotification(data.title || "Tennis", {
      body: data.body || "",
      tag: data.tag || undefined,
      renotify: Boolean(data.tag),
      icon: "/static/icons/icon-192.png",
      badge: "/static/icons/badge-96.png",
      data: { url: data.url || "/" },
    });
    // signale la réception aux pages ouvertes (test de bout en bout, rafraîchissement)
    const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const c of clients) c.postMessage({ type: "push", nonce: data.nonce, kind: data.kind });
  })());
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil((async () => {
    const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const c of clients) {
      if (new URL(c.url).origin === self.location.origin) {
        await c.focus();
        return c.navigate(url).catch(() => {});
      }
    }
    return self.clients.openWindow(url);
  })());
});

// abonnement renouvelé par le navigateur : on prévient le serveur
self.addEventListener("pushsubscriptionchange", (event) => {
  event.waitUntil((async () => {
    const res = await (await caches.open("cfg")).match("/__device");
    if (!res) return;
    const creds = await res.json();
    const cfg = await (await fetch("/api/config")).json();
    const pad = "=".repeat((4 - (cfg.vapidPublicKey.length % 4)) % 4);
    const key = Uint8Array.from(atob((cfg.vapidPublicKey + pad).replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
    const sub = event.newSubscription || await self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
    await fetch("/api/device", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-Device-Id": creds.id, "X-Device-Secret": creds.secret },
      body: JSON.stringify({ subscription: sub.toJSON() }),
    });
  })());
});
