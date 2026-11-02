#!/usr/bin/env bash
# Instalador del bot Squeeze Momentum en un servidor Linux (DigitalOcean).
# Uso (en el droplet):
#   bash <(curl -fsSL https://raw.githubusercontent.com/josemayo2/KrakenBot/smc-strategy/live/install.sh)
# Se puede volver a ejecutar sin problema: actualiza el código y respeta el .env existente.
set -euo pipefail

REPO_URL="${SQUEEZE_REPO_URL:-https://github.com/josemayo2/KrakenBot.git}"
BRANCH="${SQUEEZE_BRANCH:-smc-strategy}"
DIR="${SQUEEZE_DIR:-$HOME/squeeze-bot}"
CRON_LINE="2 * * * * cd $DIR && flock -n /tmp/squeeze_bot.lock .venv/bin/python -m live.squeeze_bot --mode live --confirm-live >> live_state/bot.log 2>&1"

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m    ✔ %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31m    ✘ %s\033[0m\n' "$*"; exit 1; }
use_tty() { [ -r /dev/tty ] && [ -z "${SQUEEZE_NO_TTY:-}" ]; }
ask() {
  local p="$1" v=""
  printf '%s' "$p" >&2
  if use_tty; then IFS= read -r v </dev/tty || true; else IFS= read -r v || true; fi
  printf '%s' "$v"
}
ask_secret() {
  local p="$1" v
  printf '%s' "$p" >&2
  if use_tty; then IFS= read -rs v </dev/tty || true; else IFS= read -r v || true; fi
  printf '\n' >&2; printf '%s' "$v"
}
yes_no() { local a; a="$(ask "$1 [s/N] ")"; [[ "$a" =~ ^[sSyY] ]]; }

# ---------------------------------------------------------------- 1. código
say "1/7 Descargando el código en $DIR"
command -v git >/dev/null || fail "git no está instalado (sudo apt install -y git)"
mkdir -p "$DIR" && cd "$DIR"
if [ ! -d .git ]; then
  git init -q && git remote add origin "$REPO_URL"
fi
git fetch -q origin "$BRANCH"
git checkout -q -B "$BRANCH" "origin/$BRANCH"
ok "código en la versión $(git rev-parse --short HEAD)"

# ---------------------------------------------------------------- 2. python
say "2/7 Preparando Python (con uv, no necesita sudo)"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 || fail "no se pudo instalar uv"
  export PATH="$HOME/.local/bin:$PATH"
fi
[ -x .venv/bin/python ] || uv venv -q .venv
uv pip install -q --python .venv/bin/python pandas numpy requests ccxt pytest
ok "Python $(.venv/bin/python -c 'import sys;print(sys.version.split()[0])') con dependencias"

# ---------------------------------------------------------------- 3. tests
say "3/7 Ejecutando los tests"
.venv/bin/python -m pytest tests -q -p no:cacheprovider | tail -1
ok "tests superados"

# ---------------------------------------------------------------- 4. .env
say "4/7 Configuración (.env)"
if [ -f .env ] && grep -q '^KRAKEN_FUTURES_API_KEY=.\+' .env; then
  ok ".env ya existe con claves; lo respeto (bórralo y vuelve a ejecutar para cambiarlas)"
else
  echo "    Pega cada valor y pulsa Enter. Las claves NO se verán en pantalla."
  KEY="$(ask_secret '    API key de Kraken Futures (cuenta de perpetuos): ')"
  SECRET="$(ask_secret '    API secret de Kraken Futures: ')"
  TG_TOKEN="$(ask_secret '    Token del bot de Telegram (vacío = sin avisos): ')"
  TG_CHAT="$(ask '    Chat ID de Telegram: ')"
  CAP="$(ask '    Capital asignado al bot en USD [1000]: ')"; CAP="${CAP:-1000}"
  SYMS="$(ask '    Mercados [PF_XBTUSD,PF_ETHUSD,PF_SOLUSD,PF_ADAUSD,PF_LINKUSD,PF_DOGEUSD]: ')"
  SYMS="${SYMS:-PF_XBTUSD,PF_ETHUSD,PF_SOLUSD,PF_ADAUSD,PF_LINKUSD,PF_DOGEUSD}"
  [ -n "$KEY" ] && [ -n "$SECRET" ] || fail "faltan la key o el secret"
  umask 077
  cat > .env <<EOF
KRAKEN_FUTURES_API_KEY=$KEY
KRAKEN_FUTURES_API_SECRET=$SECRET
TELEGRAM_TOKEN=$TG_TOKEN
TELEGRAM_CHAT_ID=$TG_CHAT
BOT_SYMBOLS=$SYMS
BOT_CAPITAL_CAP=$CAP
EOF
  chmod 600 .env
  unset KEY SECRET TG_TOKEN
  ok ".env creado (solo legible por tu usuario)"
fi
mkdir -p live_state

# ---------------------------------------------------------------- 5. comprobaciones
say "5/7 Comprobando Telegram y la conexión con Kraken (sin operar)"
.venv/bin/python - <<'PY'
from live.config import LiveConfig
from live.squeeze_bot import Notifier
c = LiveConfig.from_env("live")
Notifier(c.telegram_token, c.telegram_chat_id).send("🤖 Squeeze bot: instalación en curso, Telegram OK")
PY
.venv/bin/python -m live.squeeze_bot --mode live --status | head -20 || fail "no conecta con Kraken: revisa las claves y sus permisos"
ok "Kraken responde con estas claves"

# ---------------------------------------------------------------- 6. prueba de órdenes
say "6/7 Prueba de órdenes real con el tamaño mínimo de BTC (cuesta céntimos)"
if yes_no "    ¿Hacer la prueba ahora?"; then
  .venv/bin/python -m live.squeeze_bot --mode live --test-orders PF_XBTUSD || fail "la prueba de órdenes falló: NO actives el bot, pásale el error a Claude"
  ok "prueba de órdenes terminada (revisa que ponga 'Prueba de órdenes OK')"
else
  echo "    Prueba omitida."
fi

# ---------------------------------------------------------------- 7. cron
say "7/7 Activar el bot (cron cada hora; solo actúa al cerrar cada vela de 4h)"
if yes_no "    ¿Activar el bot con DINERO REAL ahora?"; then
  ( crontab -l 2>/dev/null | grep -v 'live.squeeze_bot' ; echo "$CRON_LINE" ) | crontab -
  ok "bot activado. Línea de cron:"
  echo "      $CRON_LINE"
else
  echo "    No activado. Para activarlo más tarde, vuelve a ejecutar este instalador."
fi

cat <<EOF

Listo. Comandos útiles (en $DIR):
  .venv/bin/python -m live.squeeze_bot --mode live --status   # capital y posiciones
  tail -50 live_state/bot.log                                 # qué ha hecho
  crontab -l                                                  # ver si está activo
  crontab -l | grep -v live.squeeze_bot | crontab -           # PARAR el bot
EOF
