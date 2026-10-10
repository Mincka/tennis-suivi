"""Envoi Web Push (VAPID). Les clés sont générées au premier démarrage dans le dossier de données."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush

from .db import DB

log = logging.getLogger(__name__)


class Pusher:
    def __init__(self, db: DB, data_dir: Path, contact: str):
        self.db = db
        self.contact = contact
        key_file = data_dir / "vapid_private.pem"
        if not key_file.exists():
            key = ec.generate_private_key(ec.SECP256R1())
            key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                                   serialization.PrivateFormat.PKCS8,
                                                   serialization.NoEncryption()))
            key_file.chmod(0o600)
        self.private_key_file = str(key_file)
        key = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
        raw = key.public_key().public_bytes(serialization.Encoding.X962,
                                            serialization.PublicFormat.UncompressedPoint)
        self.public_key = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    def _send_sync(self, subscription: dict, payload: dict, ttl: int) -> tuple[bool, str | None, bool]:
        """-> (ok, erreur, abonnement_mort)"""
        try:
            webpush(subscription_info=subscription, data=json.dumps(payload, ensure_ascii=False),
                    vapid_private_key=self.private_key_file,
                    vapid_claims={"sub": self.contact}, ttl=ttl,
                    headers={"Urgency": "high"}, timeout=15)
            return True, None, False
        except WebPushException as e:
            code = e.response.status_code if e.response is not None else None
            return False, f"{code}: {str(e)[:200]}", code in (404, 410)
        except Exception as e:  # réseau, etc.
            return False, f"{type(e).__name__}: {e}"[:200], False

    async def send(self, device_id: str, payload: dict, ttl: int = 3600) -> bool:
        row = self.db.one("SELECT subscription FROM devices WHERE id=?", device_id)
        if not row or not row["subscription"]:
            return False
        ok, err, dead = await asyncio.to_thread(self._send_sync, json.loads(row["subscription"]),
                                                payload, ttl)
        if ok:
            self.db.x("UPDATE devices SET push_ok=?, push_error=NULL WHERE id=?", time.time(), device_id)
        else:
            log.warning("push %s: %s", device_id[:8], err)
            if dead:  # désabonné, permission retirée ou appli supprimée
                self.db.x("UPDATE devices SET subscription=NULL, push_error=? WHERE id=?",
                          "expired", device_id)
            else:
                self.db.x("UPDATE devices SET push_error=? WHERE id=?", err, device_id)
        return ok
