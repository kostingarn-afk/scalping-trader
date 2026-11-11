#!/usr/bin/env bash
# Локальная инфраструктура проекта: PostgreSQL 15 + Kafka (KRaft, single-node).
# Запуск внутри WSL2: bash infra/local/up.sh
# Образы скачиваются автоматически; топики создаются существующим infra/kafka/create-topics.sh.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

log() { printf '\n==> %s\n' "$*"; }
warn() { printf '  ВНИМАНИЕ: %s\n' "$*"; }
die() { echo "ОШИБКА: $*" >&2; exit 1; }

# --- Предварительные проверки -------------------------------------------------

require_docker() {
  command -v docker >/dev/null 2>&1 ||
    die "docker не найден. Установка (однократно, внутри WSL2 Ubuntu): bash infra/local/bootstrap-wsl-docker.sh"

  docker info >/dev/null 2>&1 && return 0

  # docker info падает и при недоступном демоне, и при отсутствии прав на сокет — диагностика разная.
  local err=""
  err="$(docker info 2>&1 >/dev/null || true)"
  if grep -qi 'permission denied' <<<"$err"; then
    die "нет прав на /var/run/docker.sock: из Windows выполните wsl --shutdown и откройте WSL заново, чтобы применилась группа docker"
  fi

  # legacy-WSL (без systemd) не поднимает демон сам — стартуем его через NOPASSWD-правило.
  echo "  docker daemon не запущен — пробую стартовать"
  sudo -n service docker start >/dev/null 2>&1 || true
  local i
  for i in $(seq 1 15); do
    docker info >/dev/null 2>&1 && { echo "  демон запущен"; return 0; }
    sleep 1
  done

  die "docker daemon не запущен. Запустите вручную:
    внутри WSL : sudo service docker start
    из Windows : wsl -d Ubuntu -u root -e service docker start
  Если docker не установлен — bash infra/local/bootstrap-wsl-docker.sh"
}

# Занят ли порт внутри WSL (чужой процесс или чужой контейнер).
wsl_port_owner() {
  ss -ltnH 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]$1$" || return 1
  docker ps --filter "publish=$1" --format '{{.Names}}' | head -n1
}

# Слушает ли порт процесс на стороне Windows (в WSL-netns его не видно).
windows_port_listening() {
  command -v powershell.exe >/dev/null 2>&1 || return 1
  powershell.exe -NoProfile -NonInteractive -Command \
    "if (Get-NetTCPConnection -State Listen -LocalPort $1 -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" \
    >/dev/null 2>&1
}

ensure_port_free() {
  local port="$1" name="$2" container="$3" owner
  if owner="$(wsl_port_owner "$port")"; then
    [[ "$owner" == "$container" ]] && return 0
    if [[ -n "$owner" ]]; then
      die "порт ${port} занят контейнером '${owner}'. Освободите его: docker rm -f ${owner}"
    fi
    die "порт ${port} занят процессом в WSL. Освободите его или задайте другой: ${name}_PORT=<порт> bash infra/local/up.sh"
  fi

  if windows_port_listening "$port"; then
    warn "порт ${port} слушает процесс Windows (например, локальный PostgreSQL/Kafka)."
    warn "контейнер опубликует порт в WSL, но модули, запущенные ИЗ Windows, могут попасть в этот процесс,"
    warn "а не в контейнер. Запускайте модули внутри WSL или задайте другой порт."
  fi
}

ensure_network() {
  if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
    docker network create "$NETWORK_NAME" >/dev/null
  fi
}

remove_if_exists() {
  docker rm -f "$1" >/dev/null 2>&1 || true
}

# Опрос готовности с дедлайном: wait_until <сек> <описание> <команда...>
wait_until() {
  local timeout="$1" desc="$2"
  shift 2
  local start=$SECONDS
  until "$@" >/dev/null 2>&1; do
    if ((SECONDS - start >= timeout)); then
      echo "  ✗ таймаут ${timeout}s: ${desc}" >&2
      return 1
    fi
    sleep 2
  done
  echo "  ✓ ${desc}"
}

warn_on_creds_mismatch() {
  [[ "$PG_DB" == "scalping" && "$PG_USER" == "scalping" && "$PG_PASSWORD" == "scalping" ]] && return 0
  warn "креды БД отличаются от дефолтов конфигов модулей (scalping/scalping/scalping)."
  warn "Передайте модулям: DB_USER=${PG_USER} DB_PASSWORD=<пароль> DB_URL=jdbc:postgresql://localhost:${PG_PORT}/${PG_DB}"
  warn "Analyzer: db_url=postgresql://${PG_USER}:<пароль>@localhost:${PG_PORT}/${PG_DB}"
}

# --- Запуск -------------------------------------------------------------------

require_docker
ensure_port_free "$PG_PORT" "PG" "$PG_CONTAINER"
ensure_port_free "$KAFKA_PORT" "KAFKA" "$KAFKA_CONTAINER"
ensure_network
warn_on_creds_mismatch

log "скачиваю образы"
docker pull "$POSTGRES_IMAGE"
docker pull "$KAFKA_IMAGE"

log "перезапускаю PostgreSQL (${POSTGRES_IMAGE}) на порту ${PG_PORT}"
remove_if_exists "$PG_CONTAINER"
docker run -d \
  --name "$PG_CONTAINER" \
  --network "$NETWORK_NAME" \
  --restart unless-stopped \
  -p "${PG_PORT}:5432" \
  -e POSTGRES_DB="$PG_DB" \
  -e POSTGRES_USER="$PG_USER" \
  -e POSTGRES_PASSWORD="$PG_PASSWORD" \
  -v "${PG_VOLUME}:/var/lib/postgresql/data" \
  "$POSTGRES_IMAGE" >/dev/null

log "перезапускаю Kafka (${KAFKA_IMAGE}) на порту ${KAFKA_PORT}"
if docker ps -a --format '{{.Names}}' | grep -qx "$KAFKA_CONTAINER"; then
  warn "данные Kafka хранятся в контейнере — при пересоздании лог KRaft (включая trade-results) будет потерян"
fi
remove_if_exists "$KAFKA_CONTAINER"
docker run -d \
  --name "$KAFKA_CONTAINER" \
  --network "$NETWORK_NAME" \
  --restart unless-stopped \
  -p "${KAFKA_PORT}:9092" \
  -e CLUSTER_ID="MkU3OEVBNTcwNTJENDM2Qk" \
  -e KAFKA_NODE_ID=1 \
  -e KAFKA_PROCESS_ROLES="broker,controller" \
  -e KAFKA_LISTENERS="INTERNAL://0.0.0.0:${KAFKA_INTERNAL_PORT},EXTERNAL://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093" \
  -e KAFKA_ADVERTISED_LISTENERS="INTERNAL://${KAFKA_CONTAINER}:${KAFKA_INTERNAL_PORT},EXTERNAL://localhost:${KAFKA_PORT}" \
  -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP="CONTROLLER:PLAINTEXT,INTERNAL:PLAINTEXT,EXTERNAL:PLAINTEXT" \
  -e KAFKA_INTER_BROKER_LISTENER_NAME="INTERNAL" \
  -e KAFKA_CONTROLLER_LISTENER_NAMES="CONTROLLER" \
  -e KAFKA_CONTROLLER_QUORUM_VOTERS="1@localhost:9093" \
  -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 \
  -e KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR=1 \
  -e KAFKA_TRANSACTION_STATE_LOG_MIN_ISR=1 \
  -e KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS=0 \
  -e KAFKA_AUTO_CREATE_TOPICS_ENABLE=false \
  -v "${PROJECT_ROOT}/infra/kafka/create-topics.sh:/opt/scalping/create-topics.sh:ro" \
  "$KAFKA_IMAGE" >/dev/null

log "ожидаю готовность"
wait_until "$PG_WAIT_TIMEOUT" "PostgreSQL принимает подключения" \
  docker exec "$PG_CONTAINER" pg_isready -U "$PG_USER" -d "$PG_DB" ||
  die "PostgreSQL не поднялся за ${PG_WAIT_TIMEOUT}s. Логи: docker logs ${PG_CONTAINER}"

wait_until "$KAFKA_WAIT_TIMEOUT" "Kafka отвечает на bootstrap" \
  docker exec "$KAFKA_CONTAINER" /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server "${KAFKA_CONTAINER}:${KAFKA_INTERNAL_PORT}" --list ||
  die "Kafka не поднялась за ${KAFKA_WAIT_TIMEOUT}s. Логи: docker logs ${KAFKA_CONTAINER}"

log "создаю топики (raw-market-data, trading-signals, trade-results)"
# tr -d '\r': файл монтируется с Windows-диска и при core.autocrlf=true может иметь CRLF —
# bash внутри контейнера на CRLF падает, поэтому чистим поток перед исполнением.
docker exec \
  -e KAFKA_BOOTSTRAP="${KAFKA_CONTAINER}:${KAFKA_INTERNAL_PORT}" \
  -e KAFKA_BIN=/opt/kafka/bin \
  "$KAFKA_CONTAINER" bash -c 'tr -d "\r" < /opt/scalping/create-topics.sh | bash'

# --- Итог ---------------------------------------------------------------------

log "готово"
cat <<EOF
PostgreSQL : localhost:${PG_PORT}  db=${PG_DB} user=${PG_USER} (volume ${PG_VOLUME})
Kafka      : localhost:${KAFKA_PORT} (KRaft single-node, топики созданы)
Flyway     : применяется автоматически при старте Trader

Остановить: bash infra/local/down.sh          (данные БД сохраняются)
Удалить всё: bash infra/local/down.sh --purge (вместе с volume ${PG_VOLUME})
EOF
docker ps --filter "name=scalping-" \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
