# tennis-suivi

Suivez vos joueurs et joueuses de tennis et recevez leurs scores en notification sur votre
téléphone, **sans compte et sans application à installer**. Les données viennent des tournois
publiés sur [HappyScore](https://tennis.happyscore.net/tournoi), avec l'accord du site.

Instance publique : **https://tchg.jehrhart.net**

## Ce que ça fait

- **Tournois** : tous les tournois HappyScore en cours ou à venir, avec pour chacun les matchs
  **en direct** (actualisés automatiquement tant que la page est ouverte), **à venir** (groupés
  par jour) et **terminés**, filtrables par nom.
- **Détail d'un match** : durée totale et par set, jeux de service gagnés (match et par set),
  déroulé jeu par jeu avec le serveur et les breaks.
- **Infos du tournoi** : surface, dotation, juge-arbitre (appel direct), météo du jour, chiffres
  clés et affiche.
- **Suivre un joueur ou une joueuse** (☆) : notifications pour tous ses matchs, et une fiche avec
  son historique dans chaque tournoi (victoires, défaites, matchs à venir).
- **Suivre un match précis** (🔔), en cours ou programmé : notifications pour ce match seulement.
  Le suivi est conservé quand le match passe de « programmé » à « en direct ».
- Deux niveaux de notification :

  | Niveau | Notifications |
  |---|---|
  | **Résultat** | match programmé ou reprogrammé, début du match, résultat final |
  | **Set par set** | match programmé ou reprogrammé, début du match, score à chaque fin de set, résultat final |

- **Notification de début de match** d'après l'horaire programmé : à l'heure prévue, 15 min,
  30 min ou 1 h avant (l'horaire d'un tournoi reste indicatif ; un match en retard est signalé
  « en attente »).
- Sur ordinateur, un QR code transfère les suivis vers le téléphone (masqué sur mobile).

## Fonctionnement

```
HappyScore ──(scraping poli)──> poller ──> SQLite ──> moteur d'événements ──> Web Push (VAPID)
                                                                              │
navigateur / PWA <── API FastAPI <──────────────────────────────────── service worker
```

- **Une seule collecte par tournoi**, quel que soit le nombre d'abonnés, et au rythme de ce qui
  se passe : le direct (`/direct/fragment`) toutes les 20 s seulement quand un match joue, va
  commencer dans les 30 min ou est en retard sur son horaire ; sinon toutes les 5 min en journée
  et toutes les 30 min la nuit. Matchs à venir et terminés : 3 min pendant le jeu, 15 min sinon,
  30 s juste après la fin d'un match. Hors période de tournoi, seule la liste des tournois est
  lue (toutes les 30 min).
- **Pas de compte** : le navigateur génère un identifiant et un secret aléatoires (stockés en
  local). Le serveur ne connaît que l'abonnement push de l'appareil et la liste des joueurs
  suivis.
- Les événements (début, set, fin, programmation) viennent de la comparaison de deux
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
(`mailto:` ou URL https), `POLL=0` pour désactiver la collecte, `LOG_LEVEL`,
`CLUB_FILTER` pour limiter l'instance aux tournois d'un ou plusieurs clubs (noms séparés par `;`,
tels qu'affichés sur HappyScore, par exemple `HETTANGE GRANDE TC`).

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
