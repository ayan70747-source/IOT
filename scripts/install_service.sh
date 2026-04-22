#!/usr/bin/env bash

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/iot-patient-monitor}"
SERVICE_NAME="${SERVICE_NAME:-patient-monitor.service}"
SERVICE_USER="${SERVICE_USER:-pi}"
SERVICE_GROUP="${SERVICE_GROUP:-$SERVICE_USER}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo or as root."
  exit 1
fi

if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is required but not installed."
  exit 1
fi

if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
  echo "User '${SERVICE_USER}' does not exist. Set SERVICE_USER to a valid Linux account."
  exit 1
fi

install -d -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "${APP_DIR}"

rsync -a \
  --delete \
  --exclude ".git/" \
  --exclude ".github/" \
  --exclude ".venv/" \
  --exclude ".vscode/" \
  --exclude "__pycache__/" \
  --exclude "recordings/" \
  --exclude ".env" \
  "${REPO_ROOT}/" "${APP_DIR}/"

python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/pip" install --upgrade pip
"${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

created_env=0
if [[ ! -f "${APP_DIR}/.env" ]]; then
  install -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" -m 600 "${APP_DIR}/.env.example" "${APP_DIR}/.env"
  created_env=1
fi

tmp_service_file="$(mktemp)"
sed \
  -e "s|User=pi|User=${SERVICE_USER}|" \
  -e "s|Group=pi|Group=${SERVICE_GROUP}|" \
  -e "s|/opt/iot-patient-monitor|${APP_DIR}|g" \
  "${APP_DIR}/deploy/patient-monitor.service" > "${tmp_service_file}"

install -m 644 "${tmp_service_file}" "/etc/systemd/system/${SERVICE_NAME}"
rm -f "${tmp_service_file}"

chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${APP_DIR}"

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"

if [[ "${created_env}" -eq 1 ]]; then
  echo "Created ${APP_DIR}/.env from .env.example. Update it with your Azure connection strings, then run:"
  echo "  sudo systemctl start ${SERVICE_NAME}"
  exit 0
fi

systemctl restart "${SERVICE_NAME}"
systemctl status "${SERVICE_NAME}" --no-pager