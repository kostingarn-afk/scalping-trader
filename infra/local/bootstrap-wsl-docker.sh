#!/usr/bin/env bash
# Однократная установка Docker Engine внутри WSL2 (Ubuntu) — без Docker Desktop.
# Запуск ВНУТРИ WSL: bash infra/local/bootstrap-wsl-docker.sh
# Требует прав sudo (запросит пароль). После установки: wsl --shutdown (из Windows).
set -euo pipefail

log() { printf '\n==> %s\n' "$*"; }
die() { echo "ОШИБКА: $*" >&2; exit 1; }

[[ "$(id -u)" -ne 0 ]] || ROOT_MODE=1
ROOT_MODE="${ROOT_MODE:-0}"
[[ "$ROOT_MODE" -eq 1 ]] && echo "  запуск от root: шаг usermod будет пропущен (пользователя в дистрибутиве ещё нет)"
grep -qi microsoft /proc/version || die "скрипт рассчитан на WSL2 (Ubuntu). Windows-нативная установка не поддерживается"

RESTART_REQUIRED=0
GROUP_ADDED=0

# 1. Автозапуск docker daemon.
# Store-WSL (>= 0.67.6) умеет systemd. legacy inbox WSL (kernel из MSI) не умеет ни systemd,
# ни [boot] command (проверено), поэтому там демон стартует по требованию из up.sh —
# для этого выдаётся NOPASSWD-правило на `service docker start` (см. шаг 3b).
set_boot_key() {
  local key="$1" value="$2" conf=/etc/wsl.conf
  if grep -Eqs "^[[:space:]]*${key}[[:space:]]*=[[:space:]]*${value}[[:space:]]*$" "$conf" 2>/dev/null; then
    echo "  ${key}=${value} уже задан"
    return 0
  fi
  if [[ -f "$conf" ]]; then
    sudo cp "$conf" "${conf}.bak" || return 1
    echo "  бэкап: ${conf}.bak"
  fi
  if grep -Eqs "^[[:space:]]*${key}[[:space:]]*=" "$conf" 2>/dev/null; then
    sudo sed -i -E "0,/^[[:space:]]*${key}[[:space:]]*=.*/s//${key}=${value}/" "$conf" || return 1
  elif grep -Eqs '^[[:space:]]*\[boot\]' "$conf" 2>/dev/null; then
    sudo sed -i -E "0,/^[[:space:]]*\[boot\]/s//[boot]\n${key}=${value}/" "$conf" || return 1
  else
    printf '\n[boot]\n%s=%s\n' "$key" "$value" | sudo tee -a "$conf" >/dev/null || return 1
  fi
  echo "  ${key}=${value}"
  RESTART_REQUIRED=1
}

drop_key() {
  local key="$1" conf=/etc/wsl.conf
  grep -Eqs "^[[:space:]]*${key}[[:space:]]*=" "$conf" 2>/dev/null || return 0
  sudo sed -i -E "/^[[:space:]]*${key}[[:space:]]*=/d" "$conf" || return 1
  echo "  удалён устаревший ключ ${key}"
  RESTART_REQUIRED=1
}

# Пустая секция [boot] после удаления ключей — мусор, убираем.
cleanup_boot_section() {
  local conf=/etc/wsl.conf
  grep -Eqs '^[[:space:]]*\[boot\]' "$conf" 2>/dev/null || return 0
  if awk '
    /^[[:space:]]*\[/ { inboot = ($0 ~ /^[[:space:]]*\[boot\]/); next }
    inboot && NF && $0 !~ /^[[:space:]]*[#;]/ { found = 1 }
    END { exit(found ? 0 : 1) }
  ' "$conf"; then
    return 0
  fi
  sudo sed -i -E '/^[[:space:]]*\[boot\][[:space:]]*$/d' "$conf" || return 1
  echo "  удалена пустая секция [boot]"
}

STORE_WSL=0
if command -v wsl.exe >/dev/null 2>&1 && wsl.exe --version >/dev/null 2>&1; then
  STORE_WSL=1
  log "Store-WSL обнаружен — включаю systemd (docker.service)"
  set_boot_key systemd true || die "не удалось обновить /etc/wsl.conf"
else
  log "legacy-WSL (без systemd и [boot] command) — демон будет стартовать по требованию"
  drop_key systemd || true
  # Убираем устаревший автозапуск от предыдущих версий скрипта, если он там был.
  if grep -Eqs '^[[:space:]]*command[[:space:]]*=[[:space:]]*service docker start' /etc/wsl.conf 2>/dev/null; then
    drop_key command || true
  fi
fi
cleanup_boot_section || true

# 2. Docker Engine из официального репозитория; fallback — пакет docker.io из Ubuntu.
install_docker_official() {
  sudo apt-get update -qq || return 1
  sudo apt-get install -y -qq ca-certificates curl gnupg >/dev/null || return 1
  sudo install -m 0755 -d /etc/apt/keyrings || return 1
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg |
    sudo gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg || return 1
  sudo chmod a+r /etc/apt/keyrings/docker.gpg || return 1
  # shellcheck disable=SC1091
  . /etc/os-release || return 1
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" |
    sudo tee /etc/apt/sources.list.d/docker.list >/dev/null || return 1
  sudo apt-get update -qq || return 1
  sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null || return 1
}

install_docker_ubuntu() {
  # docker.list без ключа ломает apt-get update — убираем свой же файл.
  sudo rm -f /etc/apt/sources.list.d/docker.list
  sudo apt-get update -qq || return 1
  sudo apt-get install -y -qq docker.io >/dev/null || return 1
}

if command -v docker >/dev/null 2>&1 && docker --version >/dev/null 2>&1; then
  log "Docker уже установлен: $(docker --version)"
else
  log "устанавливаю Docker Engine (download.docker.com)"
  if ! install_docker_official; then
    log "официальный репозиторий недоступен — ставлю docker.io из Ubuntu"
    install_docker_ubuntu || die "не удалось установить Docker (проверьте сеть и apt)"
  fi
fi

# 3. Пользователь в группе docker — чтобы не писать sudo перед каждым docker.
log "настраиваю группу docker"
if [[ "$ROOT_MODE" -eq 1 ]]; then
  echo "  пропущено (root): добавьте обычного пользователя позже — usermod -aG docker <user>"
elif id -nG "$USER" | tr ' ' '\n' | grep -qx docker; then
  echo "  ${USER} уже в группе docker"
else
  sudo usermod -aG docker "$USER" || die "usermod не выполнен"
  GROUP_ADDED=1
fi

# 3b. legacy-WSL: разрешаем членам группы docker запускать демон без пароля.
# Членство в группе docker уже эквивалентно root, поэтому правило прав не расширяет.
if [[ "$STORE_WSL" -eq 0 ]]; then
  log "разрешаю запуск docker daemon без пароля (группа docker)"
  printf '%%docker ALL=(root) NOPASSWD: /usr/sbin/service docker start, /usr/sbin/service docker restart, /usr/sbin/service docker status\n' |
    sudo tee /etc/sudoers.d/90-docker-start >/dev/null || die "не удалось записать /etc/sudoers.d/90-docker-start"
  sudo chmod 0440 /etc/sudoers.d/90-docker-start || true
  sudo visudo -cf /etc/sudoers.d/90-docker-start >/dev/null || die "sudoers-файл невалиден"
  echo "  /etc/sudoers.d/90-docker-start"
fi

# 4. Запуск демона сейчас (systemd активен только после перезапуска WSL).
# legacy-WSL: ядро 5.10.16 из MSI не поддерживает часть nf_tables-модулей, и dockerd
# падает на iptables-nft ("Couldn't load match addrtype") — переключаемся на legacy-бэкенд.
if [[ "$STORE_WSL" -eq 0 && -x /usr/sbin/iptables-legacy ]]; then
  log "legacy-WSL: переключаю iptables на legacy-бэкенд"
  sudo update-alternatives --set iptables /usr/sbin/iptables-legacy >/dev/null || true
  sudo update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy >/dev/null || true
  echo "  $(iptables -V 2>/dev/null || echo 'iptables: ?')"
fi

log "запускаю docker daemon"
if [[ "$(ps -p 1 -o comm= 2>/dev/null)" == "systemd" ]]; then
  sudo systemctl enable --now docker || RESTART_REQUIRED=1
else
  sudo service docker start || true
fi

for _ in 1 2 3 4 5 6 7 8; do
  sudo docker info >/dev/null 2>&1 && break
  sleep 2
done

if sudo docker info >/dev/null 2>&1; then
  echo "  docker daemon работает"
else
  echo "  ВНИМАНИЕ: docker daemon не отвечает — потребуется перезапуск WSL (см. ниже)"
  RESTART_REQUIRED=1
fi

log "готово"
if [[ "$RESTART_REQUIRED" -eq 1 || "$GROUP_ADDED" -eq 1 ]]; then
  cat <<'EOF'
Выполните в PowerShell (Windows), затем откройте WSL заново:
  wsl --shutdown

После перезапуска проверка:
  docker info
  docker compose version
Затем:
  bash infra/local/up.sh
EOF
else
  echo "Дальше: bash infra/local/up.sh"
fi
