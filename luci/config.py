"""Rutas, configuración y credenciales de Luci.

- Configuración del usuario: ~/.config/luci/  (config.toml, persona.md, .env)
- Modelos y datos:          ~/Library/Application Support/Luci/
- Registros:                ~/Library/Logs/Luci/

Los valores por defecto viven en el paquete (luci/predeterminado/). La primera vez se
copian a ~/.config/luci/ para que puedas editarlos; al actualizar Luci, las opciones
nuevas se toman de los valores por defecto sin pisar las tuyas.
"""

from __future__ import annotations

import json
import os
import shutil
import tomllib
from pathlib import Path

PAQUETE = Path(__file__).resolve().parent
PREDETERMINADO = PAQUETE / "predeterminado"

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "luci"
DATOS = Path.home() / "Library/Application Support/Luci"
REGISTROS = Path.home() / "Library/Logs/Luci"

ID_SERVICIO = "dev.luci.asistente"          # identificador de la app y del LaunchAgent
SOCKET = DATOS / "luci.sock"                # comunicación entre servicio e interfaz
APP = Path.home() / "Applications/Luci.app"
PLIST = Path.home() / f"Library/LaunchAgents/{ID_SERVICIO}.plist"
ARCHIVO_CLAVE = CONFIG / ".env"


def preparar_config():
    """Crea ~/.config/luci con los archivos por defecto que falten."""
    CONFIG.mkdir(parents=True, exist_ok=True)
    for nombre in ("config.toml", "persona.md"):
        destino = CONFIG / nombre
        if not destino.exists():
            shutil.copy(PREDETERMINADO / nombre, destino)


def _mezclar(base: dict, propio: dict) -> dict:
    resultado = dict(base)
    for k, v in propio.items():
        resultado[k] = _mezclar(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return resultado


def cargar_config() -> dict:
    preparar_config()
    with open(PREDETERMINADO / "config.toml", "rb") as f:
        base = tomllib.load(f)
    with open(CONFIG / "config.toml", "rb") as f:
        return _mezclar(base, tomllib.load(f))


def cargar_persona() -> str:
    preparar_config()
    return (CONFIG / "persona.md").read_text(encoding="utf-8").strip()


def ruta_datos(relativa: str) -> Path:
    """Rutas de config.toml (p. ej. modelos): relativas a ~/Library/Application Support/Luci."""
    p = Path(relativa).expanduser()
    return p if p.is_absolute() else DATOS / p


def _leer_env() -> dict[str, str]:
    """Lee el archivo ~/.config/luci/.env (CLAVE=valor)."""
    valores: dict[str, str] = {}
    if ARCHIVO_CLAVE.exists():
        for linea in ARCHIVO_CLAVE.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                k, v = linea.split("=", 1)
                valores[k.strip()] = v.strip().strip('"').strip("'")
    return valores


def guardar_clave(clave: str):
    CONFIG.mkdir(parents=True, exist_ok=True)
    ARCHIVO_CLAVE.write_text(f"OPENCODE_GO_API_KEY={clave.strip()}\n", encoding="utf-8")
    ARCHIVO_CLAVE.chmod(0o600)


def buscar_clave() -> str | None:
    """Clave de OpenCode Go: variable de entorno, ~/.config/luci/.env o, por último,
    la clave con la que inició sesión OpenCode (si está instalado)."""
    clave = os.environ.get("OPENCODE_GO_API_KEY") or _leer_env().get("OPENCODE_GO_API_KEY")
    if clave:
        return clave
    auth = Path.home() / ".local/share/opencode/auth.json"
    if auth.exists():
        try:
            return json.loads(auth.read_text())["opencode-go"]["key"]
        except (KeyError, json.JSONDecodeError):
            pass
    return None


def clave_opencode_go() -> str:
    clave = buscar_clave()
    if not clave:
        raise RuntimeError(
            f"No encontré la clave de OpenCode Go. Créala en la consola de OpenCode y guárdala "
            f"en {ARCHIVO_CLAVE} como OPENCODE_GO_API_KEY=… (o ejecuta «luci configurar»)."
        )
    return clave
