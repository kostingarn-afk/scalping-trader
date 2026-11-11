"""Метрики Analyzer (задача 3.8) — prometheus_client."""
from __future__ import annotations

from prometheus_client import Counter, Histogram, Gauge, start_http_server

signals_generated = Counter(
    "signals_generated_total", "Total signals generated (BUY/SELL)"
)
signals_rejected = Counter(
    "signals_rejected_total", "Total signals rejected (commission filter / hold)"
)
analysis_latency = Histogram(
    "analysis_latency_seconds", "Analysis latency per message"
)
consumer_lag = Gauge(
    "consumer_lag", "Kafka consumer lag", labelnames=("partition",)
)


def start_metrics_server(port: int = 8000) -> None:
    start_http_server(port)
