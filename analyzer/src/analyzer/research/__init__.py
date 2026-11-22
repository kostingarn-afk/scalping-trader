"""Единицы измерения, шаг цены, сессии, целостность данных и дисперсии исследования.

Пакет этапа R0 (`to_do.md` §5): офлайн-анализ выгрузок Collector. Production-модули
(`consumer`, `strategy`, `indicators`) не импортируются, Kafka и БД не используются.
"""
from __future__ import annotations

__all__ = [
    "costs",
    "integrity",
    "labels",
    "registry",
    "sessions",
    "ticks",
    "units",
    "variance",
]
