#!/usr/bin/env bash
# Общие параметры локальной инфраструктуры (PostgreSQL + Kafka в Docker).
# Подключается из up.sh / down.sh: source "$(dirname "${BASH_SOURCE[0]}")/env.sh".
# Любое значение можно переопределить переменной окружения при запуске.
set -euo pipefail

LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${LOCAL_DIR}/../.." && pwd)"

# --- Образы ---
# Kafka 3.9.x, а не 4.x: analyzer использует kafka-python ^2.0, Java-модули — kafka-clients 3.7.1 (pom.xml).
POSTGRES_IMAGE="${POSTGRES_IMAGE:-postgres:15-alpine}"
KAFKA_IMAGE="${KAFKA_IMAGE:-apache/kafka:3.9.2}"

# --- Имена, сеть, порты ---
NETWORK_NAME="${NETWORK_NAME:-scalping-net}"
PG_CONTAINER="${PG_CONTAINER:-scalping-postgres}"
KAFKA_CONTAINER="${KAFKA_CONTAINER:-scalping-kafka}"
PG_VOLUME="${PG_VOLUME:-scalping-pgdata}"
PG_PORT="${PG_PORT:-5432}"
KAFKA_PORT="${KAFKA_PORT:-9092}"

# --- Креды PostgreSQL: должны совпадать с дефолтами collector/trader/analyzer ---
PG_DB="${PG_DB:-scalping}"
PG_USER="${PG_USER:-scalping}"
PG_PASSWORD="${PG_PASSWORD:-scalping}"

# --- Таймауты ожидания готовности, сек ---
PG_WAIT_TIMEOUT="${PG_WAIT_TIMEOUT:-60}"
KAFKA_WAIT_TIMEOUT="${KAFKA_WAIT_TIMEOUT:-120}"

# Внутренний listener Kafka (межброкерный обмен и вызовы CLI внутри контейнера).
KAFKA_INTERNAL_PORT="${KAFKA_INTERNAL_PORT:-29092}"
