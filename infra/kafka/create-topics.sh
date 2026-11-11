#!/usr/bin/env bash
# Создание Kafka-топиков для scalping-trading-system.
# Запускать при поднятом Kafka. Параметры подключения через ENV:
#   KAFKA_BOOTSTRAP (default localhost:9092)
#   KAFKA_BIN       (default /opt/kafka/bin)
set -euo pipefail

KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:-localhost:9092}"
KAFKA_BIN="${KAFKA_BIN:-/opt/kafka/bin}"

create_topic() {
  local name="$1" partitions="$2" rf="$3" retention="$4" cleanup="$5"
  echo "Creating topic ${name} (partitions=${partitions}, rf=${rf}, retention=${retention}, cleanup=${cleanup})"
  "${KAFKA_BIN}/kafka-topics.sh" --bootstrap-server "${KAFKA_BOOTSTRAP}" \
    --create --if-not-exists \
    --topic "${name}" \
    --partitions "${partitions}" \
    --replication-factor "${rf}" \
    --config "retention.ms=${retention}" \
    --config "cleanup.policy=${cleanup}"
}

# raw-market-data: 3 мин retention, delete cleanup.
# 3 партиции — по числу инструментов (partitioning по instrument_id/figi).
create_topic "raw-market-data" 3 1 180000 "delete"

# trading-signals: 3 дня retention, delete cleanup.
create_topic "trading-signals" 3 1 259200000 "delete"

# trade-results: бесконечное хранение, compact cleanup (key=trade_id).
create_topic "trade-results" 3 1 -1 "compact"

echo
echo "Topics created:"
"${KAFKA_BIN}/kafka-topics.sh" --bootstrap-server "${KAFKA_BOOTSTRAP}" --list
