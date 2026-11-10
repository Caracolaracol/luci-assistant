"""Instalación y control del servicio de Luci en macOS.

  luci configurar   descarga modelos, pide la clave, crea ~/Applications/Luci.app y la
                    inicia al iniciar sesión
  luci actualizar   instala la última versión desde GitHub
  luci desinstalar  quita la app y el inicio automático
  luci iniciar | detener | reiniciar | estado | registro
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .cliente import servicio_activo
from .config import (APP, ARCHIVO_CLAVE, CONFIG, DATOS, ID_SERVICIO, PLIST, REGISTROS, SOCKET,
                     buscar_clave, guardar_clave, preparar_config)

EJECUTABLE = "luci-servicio"
REGISTRO = REGISTROS / "luci.log"
DOMINIO = f"gui/{os.getuid()}"

MODELOS = {
    "models/piper/es_MX-claude-high.onnx":
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_MX/claude/high/es_MX-claude-high.onnx",
    "models/piper/es_MX-claude-high.onnx.json":
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_MX/claude/high/es_MX-claude-high.onnx.json",
    "models/silero/silero_vad.onnx":
        "https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx",
    "models/smart-turn/smart-turn-v3.2-cpu.onnx":
        "https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/main/smart-turn-v3.2-cpu.onnx",
}


def _launchctl(*args: str, silencioso: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True,
                          check=False) if silencioso else subprocess.run(["launchctl", *args], check=False)


def _cargado() -> bool:
    return _launchctl("print", f"{DOMINIO}/{ID_SERVICIO}", silencioso=True).returncode == 0


def descargar_modelos():
    for relativa, url in MODELOS.items():
        destino = DATOS / relativa
        if destino.exists():
            continue
        destino.parent.mkdir(parents=True, exist_ok=True)
        print(f"Descargando {relativa}…")
        temporal = destino.with_suffix(destino.suffix + ".parcial")
        urllib.request.urlretrieve(url, temporal)
        temporal.rename(destino)


def _crear_app(python: str):
    contenido = APP / "Contents"
    if APP.exists():
        shutil.rmtree(APP)
    (contenido / "MacOS").mkdir(parents=True)
    (contenido / "Resources").mkdir()

    info = {
        "CFBundleIdentifier": ID_SERVICIO,
        "CFBundleName": "Luci",
        "CFBundleDisplayName": "Luci",
        "CFBundleExecutable": EJECUTABLE,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.1",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "13.0",
        "LSUIElement": True,      # sin icono en el Dock
        "NSMicrophoneUsageDescription":
            "Luci escucha el micrófono para conversar contigo por voz. "
            "El audio se procesa en este Mac.",
    }
    with open(contenido / "Info.plist", "wb") as f:
        plistlib.dump(info, f)
    (contenido / "Resources/python").write_text(python + "\n")

    fuente = Path(__file__).parent / "app/lanzador.c"
    subprocess.run(["cc", "-O2", "-Wall", "-o", str(contenido / "MacOS" / EJECUTABLE), str(fuente)],
                   check=True)
    # Firma local (ad hoc): macOS asocia el permiso de micrófono a esta firma.
    subprocess.run(["codesign", "--force", "--sign", "-", "--identifier", ID_SERVICIO, str(APP)],
                   check=True, capture_output=True)


def _crear_plist():
    REGISTROS.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": ID_SERVICIO,
        "ProgramArguments": [str(APP / "Contents/MacOS" / EJECUTABLE)],
        "AssociatedBundleIdentifiers": [ID_SERVICIO],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},   # se reinicia si falla, no si se apaga
        "ProcessType": "Interactive",             # audio en tiempo real: sin recortes de energía
        "ThrottleInterval": 10,
        "StandardOutPath": str(REGISTRO),
        "StandardErrorPath": str(REGISTRO),
        "EnvironmentVariables": {
            "PYTHONUNBUFFERED": "1",
            "HF_HUB_DISABLE_PROGRESS_BARS": "1",
            "TQDM_DISABLE": "1",
        },
    }
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    with open(PLIST, "wb") as f:
        plistlib.dump(plist, f)


def _esperar_servicio(segundos: float = 60) -> bool:
    fin = time.time() + segundos
    while time.time() < fin:
        if servicio_activo():
            return True
        time.sleep(0.5)
    return False


# ------------------------------------------------------------------ comandos
REPOSITORIO = "git+https://github.com/caracolaracol/luci-assistant"


def _pedir_clave():
    """Pide la clave de OpenCode Go si no hay ninguna configurada."""
    if buscar_clave():
        return
    if not sys.stdin.isatty():
        print(f"Aviso: falta la clave de OpenCode Go. Guárdala en {ARCHIVO_CLAVE} "
              "como OPENCODE_GO_API_KEY=… y ejecuta «luci reiniciar».")
        return
    import getpass

    print("Luci usa OpenCode Go como cerebro. Crea una clave en https://opencode.ai (sección Go).")
    clave = getpass.getpass("Pega tu clave de OpenCode Go (no se mostrará): ").strip()
    if clave:
        guardar_clave(clave)
        print(f"Clave guardada en {ARCHIVO_CLAVE}")
    else:
        print("Sin clave: Luci escuchará, pero no podrá responder hasta que la configures.")


def configurar():
    """Deja Luci lista: configuración, modelos, clave, Luci.app e inicio automático."""
    if sys.platform != "darwin":
        raise SystemExit("Luci solo funciona en macOS.")
    preparar_config()
    print(f"Configuración: {CONFIG}")
    descargar_modelos()
    _pedir_clave()
    if _cargado():
        _launchctl("bootout", f"{DOMINIO}/{ID_SERVICIO}", silencioso=True)
        time.sleep(1)
    print(f"Creando {APP}…")
    _crear_app(sys.executable)
    _crear_plist()
    _launchctl("bootstrap", DOMINIO, str(PLIST))
    print("Luci se iniciará sola cada vez que inicies sesión.")
    print("Si macOS pregunta si «Luci» puede usar el micrófono, pulsa Permitir.")
    print("Esperando a que cargue (la primera vez descarga Whisper, ~500 MB)…")
    if _esperar_servicio(600):
        print("Listo. Abre la interfaz con: luci")
    else:
        print(f"El servicio no respondió. Revisa el registro: luci registro  ({REGISTRO})")


def actualizar():
    """Instala la última versión desde GitHub y reinicia el servicio."""
    resultado = subprocess.run(["uv", "tool", "install", "--force", REPOSITORIO], check=False)
    if resultado.returncode != 0:
        raise SystemExit("No pude actualizar Luci.")
    if PLIST.exists():
        reiniciar()


def desinstalar():
    if _cargado():
        _launchctl("bootout", f"{DOMINIO}/{ID_SERVICIO}", silencioso=True)
    PLIST.unlink(missing_ok=True)
    if APP.exists():
        shutil.rmtree(APP)
    SOCKET.unlink(missing_ok=True)
    print("Se quitaron Luci.app y el inicio automático.")
    print("Para terminar de borrar Luci:")
    print("  uv tool uninstall luci-assistant")
    print(f'  rm -rf "{CONFIG}" "{DATOS}" "{REGISTROS}"   # configuración, modelos y registros')
    print(f"  tccutil reset Microphone {ID_SERVICIO}             # permiso de micrófono")


def iniciar(esperar: bool = True):
    if not PLIST.exists():
        raise SystemExit("Luci no está configurada. Ejecuta: luci configurar")
    if servicio_activo():
        print("Luci ya está corriendo.")
        return
    if _cargado():
        _launchctl("kickstart", f"{DOMINIO}/{ID_SERVICIO}", silencioso=True)
    else:
        _launchctl("bootstrap", DOMINIO, str(PLIST))
    if esperar:
        print("Iniciando Luci…")
        print("Listo." if _esperar_servicio(120) else "No respondió todavía: revisa «luci registro».")


def detener():
    if _cargado():
        _launchctl("bootout", f"{DOMINIO}/{ID_SERVICIO}", silencioso=True)
        print("Luci detenido (volverá a iniciarse en tu próximo inicio de sesión).")
    else:
        print("Luci no estaba corriendo como servicio.")


def reiniciar():
    if _cargado():
        _launchctl("kickstart", "-k", f"{DOMINIO}/{ID_SERVICIO}", silencioso=True)
        print("Reiniciando Luci…")
        print("Listo." if _esperar_servicio(120) else "No respondió todavía: revisa «luci registro».")
    else:
        iniciar()


def estado():
    instalado = PLIST.exists() and APP.exists()
    print(f"instalado:  {'sí' if instalado else 'no'}")
    if _cargado():
        info = _launchctl("print", f"{DOMINIO}/{ID_SERVICIO}", silencioso=True).stdout
        pid = next((l.split("=")[1].strip() for l in info.splitlines() if l.strip().startswith("pid =")), "—")
        print(f"cargado:    sí (pid {pid})")
    else:
        print("cargado:    no")
    print(f"responde:   {'sí' if servicio_activo() else 'no'}")
    print(f"registro:   {REGISTRO}")


def registro(seguir: bool = False):
    if not REGISTRO.exists():
        print("Todavía no hay registro.")
        return
    if seguir:
        os.execvp("tail", ["tail", "-n", "40", "-f", str(REGISTRO)])
    subprocess.run(["tail", "-n", "60", str(REGISTRO)], check=False)
