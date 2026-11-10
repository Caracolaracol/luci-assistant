#!/bin/sh
# Instalador de Luci — asistente de voz para macOS
#
#   curl -fsSL https://raw.githubusercontent.com/caracolaracol/luci-assistant/main/instalar.sh | sh
#
# Qué hace:
#   1. Comprueba que sea un Mac con Apple Silicon y que estén las herramientas de compilación.
#   2. Instala uv (gestor de Python) si no lo tienes.
#   3. Instala el comando `luci` desde GitHub.
#   4. Ejecuta `luci configurar`: descarga modelos, pide tu clave de OpenCode Go,
#      crea ~/Applications/Luci.app y la deja iniciándose al iniciar sesión.
set -eu

REPOSITORIO="git+https://github.com/caracolaracol/luci-assistant"

info()  { printf '\033[1;33m🪲 %s\033[0m\n' "$1"; }
error() { printf '\033[1;31m✖ %s\033[0m\n' "$1" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || error "Luci solo funciona en macOS."
[ "$(uname -m)" = "arm64" ]  || error "Luci necesita un Mac con Apple Silicon (M1 o posterior)."

if ! xcode-select -p >/dev/null 2>&1; then
    info "Faltan las herramientas de línea de comandos de Xcode. Se abrirá el instalador de Apple."
    xcode-select --install || true
    error "Cuando termine esa instalación, vuelve a ejecutar este comando."
fi

if ! command -v uv >/dev/null 2>&1; then
    info "Instalando uv…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

info "Instalando Luci…"
uv tool install --force --python 3.12 "$REPOSITORIO"

info "Configurando Luci…"
# Se lee el teclado desde la terminal (el script llega por una tubería desde curl).
if [ -r /dev/tty ]; then
    luci configurar < /dev/tty
else
    luci configurar
fi

case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) info "Añade ~/.local/bin a tu PATH para usar el comando «luci» (uv tool update-shell)." ;;
esac
info "Listo. Abre Luci con: luci"
