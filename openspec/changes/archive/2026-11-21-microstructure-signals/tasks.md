# Tasks

## 1. Микроструктура и индикаторы

- [x] 1.1 `analyzer/src/analyzer/microstructure.py`: `classify_trade`, `VolumeBuckets` (VPIN по
  завершённым бакетам, `None` при недостатке), `MicrostructureState` (последний стакан и поток
  сделок по figi, снимок); проверка — синтетика: односторонний поток → VPIN ≈ 1, сбалансированный
  → 0, мало бакетов → `None`
- [x] 1.2 `indicators.py`: поле `Indicators.vpin`, параметр `vpin` у `compute_all`; проверка —
  тест: переданный VPIN попадает в индикаторы и `to_dict()`

## 2. Потребление в Kafka-пути

- [x] 2.1 `handler.py`: `parse_message` для `CANDLE`/`ORDER_BOOK`/`TRADE`, `parse_candle` как
  обёртка, `MicrostructureState` в `make_handler`, снимок стакана и VPIN в данных свечи;
  проверка — тесты разбора на сгенерированных protobuf-сообщениях (при наличии `*_pb2.py`) либо
  пропуск с явной причиной
- [x] 2.2 `main.py`: `compute_all` получает `bids_qty`, `asks_qty` и `vpin` из данных свечи;
  проверка — тест пайплайна с подставленным стаканом: `order_imbalance` не пуст

## 3. Модель микроструктуры

- [x] 3.1 `signal_models/microstructure.py`: `MicroConfig` (`obi_entry`, `vpin_max`),
  `MicrostructureModel` (BUY/SELL по знаку OBI, HOLD при токсичном потоке/без данных/без ATR),
  `microstructure_config` из `--model-param`; проверка — тесты четырёх сценариев спеки
- [x] 3.2 Запись `microstructure` в реестре и экспорт; проверка — `model_names()` содержит имя,
  `--strategy microstructure` доступен в CLI

## 4. Конфигурация и документация

- [x] 4.1 `config.py`: `vpin_bucket_volume`, `vpin_buckets`, `order_book_depth` с валидацией;
  проверка — тест дефолтов и отказа на неположительных значениях
- [x] 4.2 `analyzer/README.md`: потребление стакана и сделок, VPIN/OBI, ограничение бэктеста
  для модели; проверка — разделы ссылаются на реальные поля и флаги
- [x] 4.3 `brief.md` приведён в соответствие с изменёнными файлами и результатом проверки;
  проверка — сверка разделов brief с фактическим диффом

## Workflow follow-up

- Прогон тестов — по явному запросу владельца; ревью — только с разрешения.
- `openspec validate microstructure-signals`; архив — после приёмки.
