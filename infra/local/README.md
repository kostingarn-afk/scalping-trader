# Локальная инфраструктура: PostgreSQL 15 + Kafka (без Docker Desktop)

Два образа — `postgres:15-alpine` и `apache/kafka:3.9.2` — запускаются bash-скриптами через
**Docker Engine внутри WSL2**. Docker Desktop не устанавливается и не нужен.

| Файл | Назначение |
|---|---|
| `bootstrap-wsl-docker.sh` | однократная установка Docker Engine в Ubuntu (WSL2) |
| `up.sh` | скачать образы, запустить контейнеры, дождаться готовности, создать Kafka-топики |
| `down.sh` | остановить контейнеры (`--purge` — вместе с volume БД и сетью) |
| `env.sh` | параметры по умолчанию (образы, порты, креды) — переопределяются переменными окружения |

Скрипты используют `bash` (`[[ ]]`, `source`, `pipefail`), запускать через `sh` нельзя.

## 1. Однократная установка (Windows)

Требуются права администратора и перезагрузка — PowerShell **от администратора**:

```powershell
wsl --install -d Ubuntu
```

После перезагрузки Ubuntu запустится сама: задайте пользователя и пароль.
Проверьте, что дистрибутив работает на WSL2 (запомните имя — оно нужно дальше):

```powershell
wsl -l -v            # STATE=Running, VERSION=2
```

Затем внутри Ubuntu (`<repo>` — путь к репозиторию, например
`/mnt/c/Users/<user>/working/trader/scalping_trader_online`):

```bash
cd <repo>
bash infra/local/bootstrap-wsl-docker.sh
```

Скрипт: включает автозапуск демона (`systemd` в Store-WSL или разрешение на `service docker start`
в legacy-WSL), ставит `docker-ce` (при недоступности download.docker.com — `docker.io` из Ubuntu),
добавляет пользователя в группу `docker`, при legacy-WSL переключает iptables на legacy-бэкенд,
запускает демон. Повторный запуск безопасен.

Далее из PowerShell:

```powershell
wsl --shutdown
```

Проверка: `docker info` внутри Ubuntu должен отвечать без `sudo`
(на legacy-WSL демон при этом ещё не запущен — его поднимет `up.sh`).

### legacy-WSL (Windows 10 + ядро из MSI) — чем отличается

Проверено на этом окружении: Windows 10 22H2 19045.3570, Ubuntu 22.04.1, ядро WSL2 5.10.16,
Docker Engine 29.9.3. Отличия от Store-WSL:

| | Store-WSL | legacy-WSL (этот случай) |
|---|---|---|
| `systemd` | есть, `[boot] systemd=true` | **нет**, PID 1 = `init` |
| `[boot] command` | работает | **игнорируется** (проверено маркер-файлом) |
| Автозапуск демона | `systemctl enable docker` | по требованию: `up.sh` вызывает `sudo -n service docker start` |
| Как разрешён старт без пароля | — | `/etc/sudoers.d/90-docker-start` (только `service docker start/restart/status`, для группы `docker`) |
| iptables | nftables | **legacy** (`update-alternatives`): ядро 5.10.16 не содержит нужных nf_tables-модулей, иначе dockerd падает на `Couldn't load match addrtype` |

Практическое следствие: после `wsl --shutdown` демон не поднимается сам — его стартует `up.sh`
(или вручную `sudo service docker start`). Пароль sudo для пользователя дистрибутива нужно задать
отдельно, если он не задавался при первичной настройке:

```bash
wsl -d Ubuntu -u root -e passwd <user>
```

Проверить, какая версия WSL установлена:

```bash
wsl.exe --version >/dev/null 2>&1 && echo "Store-WSL" || echo "legacy-WSL"
```

## 2. Запуск инфраструктуры

Внутри WSL (скрипт сам поднимет docker daemon, если тот не запущен):

```bash
cd <repo>
bash infra/local/up.sh
```

Из Windows без входа в WSL (PowerShell/cmd, `<Distro>` — имя из `wsl -l -v`):

```powershell
wsl -d <Distro> -e bash -lc "cd <repo> && bash infra/local/up.sh"
```

`up.sh` идемпотентен: контейнеры пересоздаются, volume БД сохраняется, топики создаются
через существующий [`infra/kafka/create-topics.sh`](../kafka/create-topics.sh) (`--if-not-exists`).

Итог:

```
PostgreSQL : localhost:5432  db=scalping user=scalping (volume scalping-pgdata)
Kafka      : localhost:9092  (KRaft single-node)
```

Совпадает с дефолтами `trader/src/main/resources/application.yml`,
`collector/src/main/resources/application.yml`, `analyzer/src/analyzer/config.py`.
Flyway-миграции применяет сам Trader при старте — отдельный шаг не нужен.

## 3. Остановка

```bash
bash infra/local/down.sh            # контейнеры удалены, данные БД в volume сохранены
bash infra/local/down.sh --purge    # + удалить volume scalping-pgdata и сеть scalping-net
```

## 4. Параметры

Любая переменная `env.sh` переопределяется при запуске:

```bash
PG_PORT=55432 KAFKA_PORT=19092 bash infra/local/up.sh
POSTGRES_IMAGE=postgres:15.19-trixie bash infra/local/up.sh
```

Если порты или креды изменены, модулям нужно передать адреса явно (иначе они пойдут
на дефолтные `localhost:5432` / `localhost:9092` / `scalping`):

```bash
DB_URL=jdbc:postgresql://localhost:55432/scalping KAFKA_BOOTSTRAP=localhost:19092 \
  mvn -pl trader spring-boot:run        # аналогично collector
```
Для analyzer — `db_url` / `kafka_bootstrap` в его конфиге или окружении.

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `POSTGRES_IMAGE` | `postgres:15-alpine` | образ PostgreSQL (15.x — требование ТЗ) |
| `KAFKA_IMAGE` | `apache/kafka:3.9.2` | официальный образ Apache Kafka, KRaft |
| `PG_PORT` / `KAFKA_PORT` | `5432` / `9092` | порты на localhost (WSL) |
| `PG_DB` / `PG_USER` / `PG_PASSWORD` | `scalping` | креды БД из конфигов модулей |
| `PG_VOLUME` | `scalping-pgdata` | named volume с данными БД |

Kafka использует два listener'а: `INTERNAL` (`scalping-kafka:29092`, внутри docker-сети) и
`EXTERNAL` (`localhost:<KAFKA_PORT>`, для модулей на хосте).

Порты, опубликованные в WSL2, по умолчанию доступны и из Windows (`localhost:<порт>`) —
модули можно запускать с обеих сторон. Проблема возникает только если тот же порт уже слушает
служба Windows (локальный PostgreSQL/Kafka): `up.sh` предупреждает об этом, а модуль из Windows
попадёт в службу Windows, а не в контейнер. Решение — другой `PG_PORT`/`KAFKA_PORT`
или остановка службы Windows.

## 5. Диагностика

| Симптом | Причина / решение |
|---|---|
| `docker: command not found` | не выполнен шаг 1 → `bootstrap-wsl-docker.sh` |
| `Cannot connect to the Docker daemon` | на legacy-WSL автозапуска нет: `up.sh` стартует демон сам; вручную — `sudo service docker start` (из Windows: `wsl -d Ubuntu -u root -e service docker start`) |
| `permission denied ... docker.sock` | не применена группа `docker`: `wsl --shutdown`, открыть заново |
| `sudo: a password is required` | у пользователя WSL не задан пароль: `wsl -d Ubuntu -u root -e passwd <user>` |
| `$'\r': command not found` | файл сохранён с CRLF: `sed -i 's/\r$//' infra/local/*.sh` (и проверьте `.gitattributes`) |
| `source: not found` | запуск через `sh` — нужен `bash` |
| `iptables ... Couldn't load match addrtype` в `/var/log/docker.log` | nf_tables в ядре 5.10.16 неполный: `sudo update-alternatives --set iptables /usr/sbin/iptables-legacy` (то же для `ip6tables`), затем `sudo service docker restart`; bootstrap делает это сам |
| `порт 5432 занят` | локальный PostgreSQL в Windows: остановите службу или `PG_PORT=55432` |
| `таймаут Kafka` | `docker logs scalping-kafka` — обычно нехватка памяти WSL (см. ниже) |
| `read: connection reset by peer` при `docker pull` | сетевой сбой CDN: повторите `bash infra/local/up.sh`, слои докачиваются с места обрыва |

Логи: `docker logs scalping-postgres`, `docker logs scalping-kafka`.
Проверка топиков:

```bash
docker exec scalping-kafka /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server scalping-kafka:29092 --describe
```

Память WSL (по умолчанию 50 % RAM): файл `C:\Users\<user>\.wslconfig`

```ini
[wsl2]
memory=6GB
processors=4
```

после изменения — `wsl --shutdown`.

## 6. Ограничения

- Данные Kafka эфемерны: KRaft-лог живёт внутри контейнера, `up.sh` пересоздаёт контейнер и
  предупреждает об этом в выводе; топики создаются заново (для `raw-market-data` с retention
  3 минуты это несущественно, для `trade-results` — потеря при пересоздании). Данные
  PostgreSQL — в named volume и переживают перезапуск.
- Один брокер, `replication.factor=1` — только локальная разработка, не прод.
- Prometheus/Grafana в этот скрипт не входят: конфиги в [`infra/prometheus`](../prometheus/),
  [`infra/grafana`](../grafana/).
