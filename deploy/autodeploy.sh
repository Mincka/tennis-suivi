#!/usr/bin/env bash
# Déploiement continu "pull" : lancé chaque minute par tennis-suivi-deploy.timer.
# Déploie le dernier commit de main dès que le job CI "test" est vert, avec retour
# arrière si le conteneur ne répond pas. Aucun accès entrant ni secret côté GitHub.
set -euo pipefail

REPO="${REPO:-Mincka/tennis-suivi}"
DIR="${DIR:-/opt/tennis-suivi}"
PORT="${PORT:-8790}"
STATE="$DIR/.deploy"
mkdir -p "$STATE"

healthy() {
  for _ in $(seq 1 30); do
    curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}

# tout est dans main() : bash lit la fonction entière avant que le checkout ne remplace ce fichier
main() {
  remote=$(git ls-remote "https://github.com/$REPO" refs/heads/main | cut -f1)
  current=$(git -C "$DIR" rev-parse HEAD)
  [ -n "$remote" ] && [ "$remote" != "$current" ] || exit 0
  [ "$(cat "$STATE/skipped" 2>/dev/null)" != "$remote" ] || exit 0

  ci=$(curl -fsS "https://api.github.com/repos/$REPO/commits/$remote/check-runs?check_name=test" |
    python3 -c 'import json,sys; r=json.load(sys.stdin)["check_runs"]; print(r[0]["conclusion"] or "pending" if r else "pending")')
  case "$ci" in
    success) ;;
    pending) exit 0 ;;
    *) echo "CI '$ci' pour $remote : pas de déploiement"; echo "$remote" > "$STATE/skipped"; exit 0 ;;
  esac

  echo "Déploiement $current -> $remote"
  git -C "$DIR" fetch -q origin main
  git -C "$DIR" checkout -q --detach "$remote"
  mkdir -p "$DIR/data" && chown 10001 "$DIR/data"
  cd "$DIR"

  if docker compose up -d --build --remove-orphans && healthy; then
    echo "$remote" > "$STATE/deployed"
    docker image prune -f >/dev/null
    echo "OK $remote"
  else
    echo "Échec du déploiement de $remote, retour à $current"
    echo "$remote" > "$STATE/skipped"
    git -C "$DIR" checkout -q --detach "$current"
    docker compose up -d --build
    exit 1
  fi
}

main "$@"
exit $?
