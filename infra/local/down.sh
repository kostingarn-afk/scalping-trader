#!/usr/bin/env bash
# Остановка локальной инфраструктуры (PostgreSQL + Kafka).
#   bash infra/local/down.sh          — остановить и удалить контейнеры, данные БД сохранить
#   bash infra/local/down.sh --purge  — дополнительно удалить volume БД и docker-сеть
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

log() { printf '\n==> %s\n' "$*"; }

PURGE=0
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    *) echo "ОШИБКА: неизвестный аргумент '$arg' (допустимо: --purge)" >&2; exit 2 ;;
  esac
done

if ! docker info >/dev/null 2>&1; then
  echo "docker daemon недоступен — контейнеры не остановлены."
  echo "Запустите демон: sudo service docker start  (из Windows: wsl -d Ubuntu -u root -e service docker start)"
  [[ "$PURGE" -eq 1 ]] && exit 1
  exit 0
fi

log "останавливаю контейнеры"
for name in "$KAFKA_CONTAINER" "$PG_CONTAINER"; do
  if docker rm -f "$name" >/dev/null 2>&1; then
    echo "  удалён: ${name}"
  else
    echo "  нет контейнера: ${name}"
  fi
done

if [[ "$PURGE" -eq 1 ]]; then
  log "удаляю volume и сеть (--purge)"
  docker volume rm "$PG_VOLUME" >/dev/null 2>&1 || true
  if docker volume inspect "$PG_VOLUME" >/dev/null 2>&1; then
    echo "ОШИБКА: volume ${PG_VOLUME} не удалён (используется другим контейнером?)" >&2
    exit 1
  fi
  echo "  volume ${PG_VOLUME}: удалён или отсутствовал"

  docker network rm "$NETWORK_NAME" >/dev/null 2>&1 || true
  if docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
    echo "  сеть ${NETWORK_NAME}: осталась (используется другими контейнерами)"
  else
    echo "  сеть ${NETWORK_NAME}: удалена"
  fi
fi

log "готово"
