#!/usr/bin/env bash
# Одноразовая настройка запасного канала логов: agent-browser + профиль входа в auth-vault.
# Нужен, только если логи доступны лишь через SSO (SAML/ADFS) в браузере, а API без
# браузерной сессии отвечает 401.
#
# Запуск:
#   bash setup-sso-profile.sh <имя-профиля> <URL-страницы-логина>
#   bash setup-sso-profile.sh logs-sso   "https://sso.example.com/adfs/ls/"
#   bash setup-sso-profile.sh kibana-prod "https://kibana-prod.example.com/login"
#
# Пароль вводится СКРЫТО и попадает ТОЛЬКО в локальный vault agent-browser (агент его не видит).
set -euo pipefail

PROFILE="${1:-}"
LOGIN_URL="${2:-}"
if [ -z "$PROFILE" ] || [ -z "$LOGIN_URL" ]; then
  echo "использование: bash setup-sso-profile.sh <имя-профиля> <URL-страницы-логина>" >&2
  exit 1
fi

command -v agent-browser >/dev/null 2>&1 || {
  echo "agent-browser не найден — ставлю (npm)...";
  npm i -g agent-browser && agent-browser install;
}

if agent-browser auth list 2>/dev/null | grep -q "$PROFILE"; then
  echo "профиль $PROFILE уже есть — пропускаю"
  exit 0
fi

read -r -p "Логин для $LOGIN_URL: " SSO_USER
[ -n "$SSO_USER" ] || { echo "логин пустой"; exit 1; }
read -r -s -p "Пароль (ввод скрыт): " PASS
echo
[ -n "$PASS" ] || { echo "пароль пустой"; exit 1; }

printf '%s' "$PASS" | agent-browser auth save "$PROFILE" \
  --url "$LOGIN_URL" --username "$SSO_USER" --password-stdin
unset PASS

echo "Готово. Профили:"
agent-browser auth list
