# Proposal

## Why

Gate 1 закрыт: θ измерима на 4 из 20 инструментов, но `t_{1/2}` = 62.6–131.8 мин при `τ_cap = 60`
(`PROJECT-DECISION:r3-slow-reversion` в `to_do.md` §4). Следующий шаг по §13
`holding_exiting.md` — ядро решения: вероятности первого касания, `EV(τ)` с CI, выбор горизонта,
верхние границы, правило удержания, онлайн-тест валидности, таблица выходов и офлайн-исполнение
политики с журналом сделок. Без этого Gate 0b сравнивать нечего: он работает по `trade_log`.

## What Changes

- **`p_TP, p_SL, p_N`** — вероятности первого касания OU с вертикальным барьером τ; протокол
  «SL первым» (конвенция смещает EV вниз — объявлено), CI блочным bootstrap с блоком ≥ 2τ.
- **`EV(τ)`, `V(τ)`** — по сетке τ на train; `EV_LCB(τ)` получается сдвигом на нижнюю границу CI.
- **`w`** — ожидание следующего сигнала на замороженной политике, с пометкой эндогенности.
- **`τ*`** — объектив `EV/√(V(τ)·(τ+w))`; `τ* > τ_cap` → `SCOPE_VIOLATION`, а не продление.
- **Верхние границы** `τ_max = min(τ_edge, τ_regime, τ_mtf, τ_model, τ_liq, τ_session, τ_cap)`
  с правилом округления (вверх для `τ_min`-подобных, вниз для `τ_max`, кратно Δt).
- **Режим** — HMM-15m, `K = 2`, Gate 2 (`N_trans ≥ 10K(K−1)`), `γ_t(fav)`, выживание на 10m
  как вход для `τ_mtf`; при провале Gate 2 — понижение до research-only, а не подгонка.
- **Правило удержания** — `Hold ⇔ m_rem − λ·s_rem > E[C_later] − C_now`; `A_t ≤ 0` — выход.
- **Онлайн-тест валидности** — полоса `A_0^LCB·e^{−θ_LCB t} − z_{1−α/Δt}·√(1−e^{−2θ_UCB t})`,
  `α_check = α_total / n_checks`.
- **Таблица выходов** — все 11 триггеров §9 с типом (жёсткий / редукция / выход).
- **Офлайн-исполнение** — вход по §6, исходы §10, `trade_log` и `no_trade_reports`; SL первым.

Не входит: Gate 0b и CPCV/DSR (R4), runtime (R5), sandbox (R6), UI (R7).

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `research-validation`: требования EV/τ*/границ/удержания/исполнения политики.

## Impact

- Analyzer: `research/first_passage.py`, `research/ev.py`, `research/regime.py`,
  `research/holding.py`, `research/policy_exec.py`, расширение `research/cli.py`, тесты.
- Отчёты: `reports/research/ev-curve.json`, `tau-star.json`, `tau-max.json`,
  `hold-rule.json`, `online-band.json`, `exit-triggers.json`, `trade-log.json`,
  `no-trade.json` (каталог в `.gitignore`).
- БД, Kafka, Trader, Collector — не затрагиваются.

## Исполнение по волнам

Изменение крупное: 9 шагов. Волны фиксируются в `tasks.md`:
1. EV-ядро: first passage, `EV/V`, `ev_curve`, `w`, `τ*`;
2. Границы и режим: `τ_max`, HMM-15m/Gate 2, `τ_mtf`;
3. Удержание и выходы: правило, онлайн-полоса, таблица триггеров;
4. Офлайн-исполнение: `trade_log`, `no_trade_reports`, сквозной прогон.
