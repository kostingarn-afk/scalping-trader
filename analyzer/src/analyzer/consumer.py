"""Kafka consumer для raw-market-data — задача 3.2.

Multi-process: N процессов в одной consumer group, partitioning по instrument_id (figi).
"""
from __future__ import annotations

import logging
import signal
from multiprocessing import Process
from typing import Callable, List

from kafka import KafkaConsumer

from .config import AnalyzerConfig

logger = logging.getLogger(__name__)


def _create_consumer(config: AnalyzerConfig, process_id: int) -> KafkaConsumer:
    return KafkaConsumer(
        config.topic_raw,
        bootstrap_servers=config.kafka_bootstrap,
        group_id=config.consumer_group,
        enable_auto_commit=False,
        auto_offset_reset="latest",
        value_deserializer=lambda b: b,  # Protobuf — десериализация в обработчике
        key_deserializer=lambda b: b.decode("utf-8") if b else None,
        client_id=f"analyzer-{process_id}",
    )


def run_consumer(config: AnalyzerConfig, process_id: int, handler: Callable) -> None:
    """Один процесс-консьюмер. handler(message) — бизнес-логика."""
    consumer = _create_consumer(config, process_id)
    logger.info("Consumer process %d started", process_id)

    stop = False

    def _shutdown(*_):
        nonlocal stop
        stop = True
        consumer.close()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        for msg in consumer:
            try:
                handler(msg)
                consumer.commit()
            except Exception:
                logger.exception("Error processing message offset=%s", msg.offset)
    except Exception:
        if not stop:
            logger.exception("Consumer %d fatal error", process_id)
    finally:
        consumer.close()


def _run_worker(config: AnalyzerConfig, process_id: int, handler_factory: Callable) -> None:
    """Обработчик создаётся внутри процесса: asyncio-loop и пул asyncpg не наследуются."""
    run_consumer(config, process_id, handler_factory(config))


def start_workers(config: AnalyzerConfig, handler_factory: Callable, num_processes: int) -> List[Process]:
    """Запуск N процессов-консьюмеров (задача 3.2).

    @param handler_factory вызывается в каждом процессе: `handler_factory(config) -> handler(msg)`
    """
    processes = []
    for i in range(num_processes):
        p = Process(target=_run_worker, args=(config, i, handler_factory), daemon=True)
        p.start()
        processes.append(p)
        logger.info("Started analyzer worker %d (pid=%d)", i, p.pid)
    return processes


def join_workers(processes: List[Process]) -> None:
    for p in processes:
        p.join()
