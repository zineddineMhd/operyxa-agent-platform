#!/usr/bin/env bash
# Démarre toute la plateforme avec Docker : ./start.sh   (arrêt : ./start.sh stop)
set -euo pipefail
cd "$(dirname "$0")"
COMPOSE=(docker compose -f infrastructure/compose.yml)

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker est introuvable. Installez Docker Desktop : https://www.docker.com/products/docker-desktop/" >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker est installé mais ne tourne pas. Lancez Docker Desktop, attendez qu'il soit prêt, puis relancez." >&2
  exit 1
fi

case "${1:-start}" in
  stop) "${COMPOSE[@]}" stop; echo "Plateforme arrêtée. Vos données sont conservées."; exit 0 ;;
  logs) exec "${COMPOSE[@]}" logs -f --tail 100 ;;
  start) ;;
  *) echo "usage: ./start.sh [start|stop|logs]" >&2; exit 2 ;;
esac

random() { head -c 48 /dev/urandom | base64 | tr -d '/+=\n' | head -c 64; }
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Fichier .env créé."
fi
if grep -q '^JWT_SECRET_KEY=replace_me' .env || grep -q '^JWT_SECRET_KEY=$' .env; then
  sed -i.bak "s|^JWT_SECRET_KEY=.*|JWT_SECRET_KEY=$(random)|" .env && rm -f .env.bak
  echo "JWT_SECRET_KEY générée."
fi
if grep -q '^SECRETS_KEY=$' .env; then
  sed -i.bak "s|^SECRETS_KEY=.*|SECRETS_KEY=$(random)|" .env && rm -f .env.bak
  echo "SECRETS_KEY générée."
fi
if grep -q '^LLM_PROVIDER=demo' .env; then
  echo "Note : LLM_PROVIDER=demo. Pour une vraie analyse, mettez votre clé dans .env (voir le guide)."
fi

echo "Construction et démarrage (la première fois : plusieurs minutes)..."
"${COMPOSE[@]}" up -d --build

for _ in $(seq 1 90); do
  if curl -fsS http://localhost:8080/healthz >/dev/null 2>&1; then
    echo
    echo "Operyxa est prêt : http://localhost:8080"
    echo "Documentation de l'API : http://localhost:8000/docs"
    echo "Arrêt : ./start.sh stop    Journaux : ./start.sh logs"
    exit 0
  fi
  sleep 2
done
echo "La plateforme ne répond pas encore. Regardez les journaux : ./start.sh logs" >&2
exit 1
