# Tasks — research-phasea-gate0a (этап R1 плана)

## 1. Политика фазы A

- [x] 1.1 `research/policy.py`: `PolicyParams` (q, α_total, z_entry, ρ_max, impact_budget,
      τ_cap, buffer), производная `λ`, валидация диапазонов
- [x] 1.2 загрузка источников: `from_mapping` (YAML) и `from_env`, приоритет env → YAML →
      значения по умолчанию, запись источника в `policy_inputs`
- [x] 1.3 `FrozenPolicy`: барьеры `a = k_a·σ_1`, `b = k_b·σ_1` в целых тиках, сайзинг через
      `ρ_max`, одна позиция, `κ`-кап, flat-at-close, список условий no-trade
- [x] 1.4 `freeze()`: артефакты `frozen_policy` + `policy_inputs`, запись в `TrialRegistry`,
      отказ при отсутствии регистрации; `anchor_freeze_log`
- [x] 1.5 тесты `tests/test_research_policy.py`: диапазоны, приоритет источников, λ, барьеры
      в тиках, отказ незамороженной политики

## 2. Gate 0a

- [x] 2.1 `research/gate0a.py`: `σ_τ` в тиках (`σ_1·√(τ·VR)·price/tick`), `φ*(τ) = Cost/σ_τ`,
      строки `NO_DATA` для нехватки данных
- [x] 2.2 вердикт по инструменту (`PASS` / `IMPOSSIBLE`), агрегированный вердикт по прогону
- [x] 2.3 тесты `tests/test_research_gate0a.py`: точные значения `φ*` на синтетике, вердикт,
      `NO_DATA`, стресс ×1.5 не меняет вердикт

## 3. CLI и артефакты

- [x] 3.1 подкоманда `phi-star`: `--policy`, `--stress`, переиспользование `--data-dir`,
      `--out`, `--tariffs`, `--spread-bps`
- [x] 3.2 артефакт `phi_star_table` через `build_artifact`, коды возврата 0/1/2
- [x] 3.3 тесты CLI: провал на всех инструментах даёт код 1, успех — 0, запись артефакта
      (покрыто тестами разбора аргументов и прогона `phi-star` на синтетике)

## 4. Проверка

- [x] 4.1 `pytest tests -q` (513 passed), `ruff check src tests`
- [x] 4.2 реальный прогон `analyzer-research phi-star --data-dir ../data/history-1min
      --out ../reports/research --tariffs trader --spread-bps 5`: вердикт PASS,
      19 из 20 инструментов проходят, SBER — `IMPOSSIBLE` (min φ* 1.20 при τ=30)
- [x] 4.3 `openspec validate --strict research-phasea-gate0a`
- [x] 4.4 `brief.md` актуализирован по фактическому состоянию
