"""Доступ к сгенерированным protobuf-модулям.

protoc генерирует модули с абсолютными импортами друг друга (`import common_pb2`),
поэтому каталог пакета добавляется в `sys.path`: иначе `import raw_market_data_pb2`
не разрешается, когда `analyzer` импортируется как установленный пакет.

Модули генерируются build-шагом (`poetry run protoc ...`) и в git не попадают
(см. `.gitignore`: `*_pb2.py`), поэтому отсутствие модуля — штатная ситуация.
"""
from __future__ import annotations

import logging
import os
import sys
from types import ModuleType
from typing import Optional

logger = logging.getLogger(__name__)

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)


def _load(name: str) -> Optional[ModuleType]:
    try:
        return __import__(name)
    except ImportError:  # pragma: no cover — зависит от build-шага
        logger.debug("protobuf module %s is not generated", name)
        return None


common_pb2 = _load("common_pb2")
raw_market_data_pb2 = _load("raw_market_data_pb2")
trading_signal_pb2 = _load("trading_signal_pb2")
trade_result_pb2 = _load("trade_result_pb2")

PROTO_AVAILABLE = raw_market_data_pb2 is not None and trading_signal_pb2 is not None
