# Brief — research-ev-tau-star

Этап **R3** плана `to_do.md` (§5, шаги R3.1–R3.9). Вход: R0–R2 закрыты и заархивированы;
Gate 1 пройден 4/20, `θ` = 0.0053–0.0111 мин⁻¹, `t_{1/2}` = 62.6–131.8 мин при `τ_cap = 60`.

## Шаги

| № | Шаг | Артефакт |
|---|---|---|
| 1 | `first_passage.py`: `p_TP, p_SL, p_N` по 1m с конвенцией «SL первым», `EV(τ)`, `V(τ)`, CI блочным bootstrap (≥ 2τ) | `first_passage_protocol`, `ev_curve` |
| 2 | `ev.py`: `w` на замороженной политике, `τ*` по `EV/√(V(τ)(τ+w))`, `SCOPE_VIOLATION` при `τ* > τ_cap` | `tau_star_selection` |
| 3 | `regime.py`: HMM-15m, `K = 2`, Gate 2, `γ_t(fav)`, выживание на 10m | `hmm_fit`, `regime_survival`, `regime_survival_10m` |
| 4 | `ev.py`: `τ_max` из семи компонентов, округление | `tau_max_components` |
| 5 | `holding.py`: правило удержания, онлайн-полоса, таблица выходов | `hold_rule_config`, `online_band_spec`, `exit_trigger_table` |
| 6 | `policy_exec.py`: офлайн-исполнение, исходы §10 | `trade_log`, `no_trade_reports` |

## Принятые решения

- **Протокол первого касания — эмпирический по 1m, «SL первым».** Численная краевая задача
  точнее по модели, но требует доверия к OU там, где Gate 1 показал `t½ ≈ 2τ_cap`;
  эмпирика с консервативной конвенцией ближе к данным. Численный вариант — `REFERENCE_ONLY`,
  реализуется только как сверка на синтетике.
- **CI — блочный bootstrap по блокам ≥ 2τ.** Соседние исходы зависимы; iid-bootstrap занижает
  интервал. Блок задаётся в единицах баров и растёт с τ.
- **`τ*` ищется на train, `EV_LCB` — входной гейт.** Отбор по верхней оценке EV запрещён.
- **Окно оценки `θ` фиксировано из R2 (8 недель), но для EV используется train-разбиение R4.**
  В R3 границы train/OOS объявляются заранее и пишутся в артефакт, чтобы R4 не пере-выбирал их
  по результату.
- **`τ_max` считается по семи компонентам, отсутствие компонента не заменяется единицей.**
  Если компонент не идентифицирован (например, Gate 2 провален), он помечается `REFERENCE_ONLY`
  и `τ_max` берётся по остальным — с явным флагом, а не молча.
- **Правило удержания — λ-форма.** `|Z_hold| ≈ ½|Z_entry|` отменено (§7 `holding_exiting.md`).
- **Бонферрони для онлайн-полосы.** `α_check = α_total/n_checks`, `n_checks = τ_max/Δt`;
  консервативно из-за корреляции проверок, объявлено.

## Отклонённые альтернативы

- **Гейт по `p_SL`** — запрещён §12: гейт только `EV_LCB`.
- **Безбарьерные формулы как операционные** — `REFERENCE_ONLY` (C3).
- **Подстановка `σ_P` вместо `σ` в bound касания** — запрещена §12 (C1).
- **Подбор `λ`, `q`, `α` post hoc** — запрещён; значения из политики R2.

## Риски

- `t½ > τ_cap` у всех инструментов: EV на горизонте `τ ≤ 60` считается в почти линейной области
  OU. Артефакты несут `regime = "tau_ll_t_half"` и `t_half/τ_max` (`PROJECT-DECISION:r3-slow-reversion`).
- HMM-15m на 8 неделях даёт мало переходов: Gate 2 может провалиться, тогда `τ_regime`/`τ_mtf`
  уходят в `REFERENCE_ONLY`, и `τ_max` определяется оставшимися компонентами.
- Данные могут быть sandbox-симуляцией (Gate 0b заблокирован): все выводы R3 — для этой выборки.
- Пропуски 3–31 % минут: первый проход по 1m с пропусками считается по фактическим барам,
  а не по идеальной сетке; направление смещения не оценено.

## Способ проверки

- `pytest tests/test_research_first_passage.py`, `test_research_ev.py`, `test_research_regime.py`,
  `test_research_holding.py`, `test_research_policy_exec.py`.
- Синтетика с известной OU: восстановление `p_TP/p_SL`, `EV`, `V`; `τ*` на сетке; сверка
  эмпирики с численным решением (`REFERENCE_ONLY`) в пределах допуска.
- `pytest tests -q`, `ruff check src tests`, `openspec validate research-ev-tau-star --strict`.
- Реальный прогон по 4 прошедшим Gate 1 инструментам.

## Изменённые файлы (план)

| Файл | Что будет сделано |
|---|---|
| `analyzer/src/analyzer/research/first_passage.py` | барьеры, `p_*`, `EV`, `V`, CI |
| `analyzer/src/analyzer/research/ev.py` | `w`, `τ*`, `τ_max`, артефакты |
| `analyzer/src/analyzer/research/regime.py` | HMM-15m, Gate 2, `γ_t`, выживание 10m |
| `analyzer/src/analyzer/research/holding.py` | правило удержания, онлайн-полоса, выходы |
| `analyzer/src/analyzer/research/policy_exec.py` | офлайн-исполнение, `trade_log` |
| `analyzer/src/analyzer/research/cli.py` | подкоманды `ev`, `regime`, `execute` |
| `analyzer/tests/test_research_*.py` | тесты по модулям |
| `openspec/specs/research-validation/spec.md` | требования R3 (через дельту) |

## Статус актуальности

Создано 2026-11-22. Код не начат.
