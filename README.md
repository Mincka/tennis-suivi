# tennis-suivi

Suivez vos joueuses et joueurs de tennis et recevez leurs scores en notification sur votre
téléphone, **sans compte et sans application à installer**. Les données viennent des tournois
publiés sur [HappyScore](https://tennis.happyscore.net/tournoi), avec l'accord du site.

Instance publique : **https://tchg.jehrhart.net**

## Ce que ça fait

- Recherche d'un joueur dans tous les tournois HappyScore en cours ou à venir (sans accents,
  par nom et club).
- Pour chaque joueur suivi, trois niveaux de notification :

  | Niveau | Notifications |
  |---|---|
  | **Match** | match programmé ou reprogrammé, début du match, résultat final |
  | **Sets** | idem + score à la fin de chaque set |
  | **Jeux** | idem + chaque jeu |

- **Rappel avant le match** d'après l'horaire programmé : à l'heure prévue, 15 min, 30 min
  ou 1 h avant (l'horaire d'un tournoi reste indicatif).
- Sur ordinateur, un QR code transfère la sélection vers le téléphone. Il est masqué sur mobile.

## Fonctionnement

```
HappyScore ──(scraping poli)──> poller ──> SQLite ──> moteur d'événements ──> Web Push (VAPID)
                                                                              │
navigateur / PWA <── API FastAPI <──────────────────────────────────── service worker
```

- **Une seule collecte par tournoi**, quel que soit le nombre d'abonnés : le direct
  (`/direct/fragment`, que le site rafraîchit lui-même toutes les 15 s) toutes les 20 s, les
  matchs à venir et terminés toutes les 3 min, plus souvent quand un match vient de se terminer.
  Les tournois qui ne sont pas en cours sont lus au plus une fois par heure.
- **Pas de compte** : le navigateur génère un identifiant et un secret aléatoires (stockés en
  local). Le serveur ne connaît que l'abonnement push de l'appareil et la liste des joueurs
  suivis.
- Les événements (début, jeu, set, fin, programmation) viennent de la comparaison de deux
  lectures successives. L'état est persistant : un redémarrage ne renvoie pas de doublons. Au
  premier passage sur un tournoi, l'historique est importé sans notifier.

### Notifications : iOS, Android, ordinateur

| Plateforme | Ce qu'il faut |
|---|---|
| Android (Chrome, Samsung Internet, Firefox, Edge) | rien de plus : « Activer les notifications » |
| iPhone / iPad (iOS 16.4+) | ajouter la page à l'écran d'accueil (Partager → *Sur l'écran d'accueil*), puis activer depuis l'icône. La page guide l'utilisateur, et les joueurs déjà choisis sont repris par l'icône. |
| Ordinateur | Chrome, Edge, Firefox, Safari |

La page détecte et explique chaque cas bloquant :

- navigateur intégré à une appli (Instagram, Facebook, Google…) : ouvrir dans le navigateur ;
- iOS trop ancien, ou page pas encore ajoutée à l'écran d'accueil ;
- permission refusée (`Notification.permission` et `pushManager.permissionState`), avec la
  marche à suivre propre à la plateforme ; l'état est revérifié au retour sur la page ;
- abonnement expiré côté service push (réponse 404/410) : réabonnement automatique ;
- **blocage au niveau du système** (notifications du navigateur désactivées dans les réglages
  du téléphone, mode Concentration, etc.) : ce cas est invisible pour les API web. Une
  notification de test est envoyée à l'activation. Le service worker confirme sa réception à la
  page, qui demande alors « L'avez-vous vue ? ». Une réponse « non » affiche les réglages
  système à vérifier ; une non-réception signale un problème de service push (Brave, économie
  d'énergie…).

## Développement

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt   # Windows : .venv\Scripts\
DATA_DIR=.localdata uvicorn app.main:app --reload --port 8791
pytest -q && ruff check .
```

Les notifications fonctionnent sur `http://localhost` (contexte sécurisé). Les clés VAPID sont
générées au premier démarrage dans `DATA_DIR/vapid_private.pem` : les conserver, sinon tous
les abonnements deviennent invalides.

Variables d'environnement : `DATA_DIR` (défaut `data`), `APP_NAME`, `VAPID_CONTACT`
(`mailto:` ou URL https), `POLL=0` pour désactiver la collecte, `LOG_LEVEL`.

Les icônes sont générées par `python tools/make_icons.py`.

## Déploiement (prod)

Conteneur Docker derrière un tunnel Cloudflare, sur un VPS arm64.

- **CI** (GitHub Actions, `.github/workflows/ci.yml`) : ruff, pytest et `docker build` sur
  chaque push et chaque PR.
- **CD en mode pull** (`deploy/`) : sur le serveur, un timer systemd vérifie `main` chaque
  minute (`git ls-remote`). Dès que le job CI `test` du nouveau commit est vert, il fait un
  checkout de ce commit, puis `docker compose up -d --build`, puis attend `/health`. En cas
  d'échec, il revient au commit précédent. Aucun port entrant ni secret n'est nécessaire côté
  GitHub.

Installation initiale sur le serveur :

```bash
sudo git clone https://github.com/Mincka/tennis-suivi /opt/tennis-suivi
cd /opt/tennis-suivi && sudo mkdir -p data && sudo chown 10001 data
echo 'APP_NAME=Suivi Tennis' | sudo tee .env
sudo docker compose up -d --build
sudo cp deploy/tennis-suivi-deploy.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now tennis-suivi-deploy.timer
journalctl -u tennis-suivi-deploy -f   # suivi des déploiements
```

Puis exposer `http://127.0.0.1:8790` (route publique du tunnel Cloudflare, ou tout reverse
proxy HTTPS : le Web Push exige HTTPS).

## Licence

MIT. Projet indépendant, sans lien officiel avec HappyScore ni avec la FFT.
