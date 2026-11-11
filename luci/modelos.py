"""Catálogo de modelos de Luci: transcripción (Whisper MLX) y voz (Piper, macOS).

Detecta lo que ya está en el Mac, descarga lo que falte (con progreso) y guarda la
elección en ~/.config/luci/config.toml.

- Whisper MLX se guarda en la caché de Hugging Face (~/.cache/huggingface/hub), la misma
  que usan otras herramientas: si un modelo ya está ahí, Luci lo reutiliza sin descargarlo.
  Los modelos GGUF (whisper.cpp, Handy, Mantis) usan otro motor y no son compatibles.
- Las voces de Piper se guardan en ~/Library/Application Support/Luci/models/piper.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import CONFIG, DATOS, buscar_clave

CACHE_HF = Path(os.environ.get("HF_HUB_CACHE")
                or Path(os.environ.get("HF_HOME", Path.home() / ".cache/huggingface")) / "hub")
PIPER = DATOS / "models/piper"
URL_PIPER = "https://huggingface.co/rhasspy/piper-voices/resolve/main/es"


@dataclass(frozen=True)
class Modelo:
    id: str          # repo de Hugging Face (Whisper) o nombre de la voz
    nombre: str
    detalle: str
    bytes: int = 0


WHISPER = [
    Modelo("mlx-community/whisper-small-mlx-8bit", "Whisper Small · 8 bits",
           "El más liviano; transcribe muy rápido", 295_627_784),
    Modelo("mlx-community/whisper-small-mlx", "Whisper Small",
           "Rápido (~0,25 s por frase); recomendado", 481_307_592),
    Modelo("mlx-community/whisper-large-v3-turbo-q4", "Whisper Large v3 Turbo · 4 bits",
           "Más preciso, tamaño moderado", 463_664_664),
    Modelo("mlx-community/whisper-large-v3-turbo", "Whisper Large v3 Turbo",
           "El más preciso (~0,9 s por frase)", 1_613_977_612),
]

VOCES_PIPER = [
    Modelo("es_MX-claude-high", "Claude · México", "Piper, calidad alta", 63_122_309),
    Modelo("es_MX-ald-medium", "Ald · México", "Piper, calidad media", 63_201_294),
    Modelo("es_AR-daniela-high", "Daniela · Argentina", "Piper, calidad alta", 114_199_011),
    Modelo("es_ES-davefx-medium", "Davefx · España", "Piper, calidad media", 63_221_984),
    Modelo("es_ES-sharvard-medium", "Sharvard · España", "Piper, calidad media", 76_753_841),
    Modelo("es_ES-carlfm-x_low", "Carlfm · España", "Piper, la más liviana", 28_130_791),
]

VOCES_MAC = ["Paulina", "Mónica"]


# ------------------------------------------------------------------ detección
def _carpeta_hf(repo: str) -> Path:
    return CACHE_HF / ("models--" + repo.replace("/", "--"))


def whisper_instalado(repo: str) -> bool:
    """¿Está el modelo completo en la caché de Hugging Face?"""
    for snap in (_carpeta_hf(repo) / "snapshots").glob("*"):
        if (snap / "config.json").exists() and any((snap / f).exists() for f in
                                                   ("weights.npz", "weights.safetensors")):
            return True
    return False


def whisper_disponibles() -> list[Modelo]:
    """Catálogo más cualquier otro Whisper MLX que ya esté en la caché."""
    lista = list(WHISPER)
    conocidos = {m.id for m in lista}
    for carpeta in sorted(CACHE_HF.glob("models--mlx-community--whisper-*")):
        repo = carpeta.name.removeprefix("models--").replace("--", "/", 1)
        if repo not in conocidos and whisper_instalado(repo):
            lista.append(Modelo(repo, repo.split("/")[-1], "Encontrado en este Mac"))
    return lista


def piper_instalada(voz: str) -> bool:
    return (PIPER / f"{voz}.onnx").exists() and (PIPER / f"{voz}.onnx.json").exists()


def piper_disponibles() -> list[Modelo]:
    lista = list(VOCES_PIPER)
    conocidos = {m.id for m in lista}
    for f in sorted(PIPER.glob("*.onnx")):
        if f.stem not in conocidos and piper_instalada(f.stem):
            lista.append(Modelo(f.stem, f.stem, "Encontrada en este Mac"))
    return lista


def voces_mac() -> list[str]:
    try:
        salida = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    instaladas = {re.split(r"\s{2,}", l)[0].strip() for l in salida.splitlines()}
    return [v for v in VOCES_MAC if v in instaladas]


def _mascara(clave: str | None) -> str:
    return f"…{clave[-4:]}" if clave and len(clave) > 8 else ("configurada" if clave else "")


def catalogo(cfg: dict, descarga: dict | None = None, llm_disponibles: list[str] | None = None) -> dict:
    """Lo que muestran los menús: qué hay, qué está instalado y qué está en uso."""
    stt, tts, llm = cfg["stt"], cfg["tts"], cfg["llm"]
    lista = list(dict.fromkeys([llm["modelo"]] + list(llm.get("candidatos") or [])))
    voz_actual = (f"say:{tts.get('voz_say', 'Paulina')}" if tts.get("motor") == "say"
                  else Path(tts.get("voz_piper", "")).stem)
    voces = [{"id": m.id, "nombre": m.nombre, "detalle": m.detalle, "bytes": m.bytes,
              "instalado": piper_instalada(m.id), "activo": m.id == voz_actual}
             for m in piper_disponibles()]
    voces += [{"id": f"say:{v}", "nombre": f"{v} · macOS", "detalle": "Voz del sistema", "bytes": 0,
               "instalado": True, "activo": f"say:{v}" == voz_actual} for v in voces_mac()]
    return {
        "t": "catalogo",
        "stt": [{"id": m.id, "nombre": m.nombre, "detalle": m.detalle, "bytes": m.bytes,
                 "instalado": whisper_instalado(m.id), "activo": m.id == stt["modelo"]}
                for m in whisper_disponibles()],
        "voz": voces,
        # Modelos de lenguaje: primero los de la lista (en uso y respaldo, en orden), luego
        # el resto de los que ofrece OpenCode Go.
        "llm": [{"id": m, "nombre": m, "activo": m == llm["modelo"], "respaldo": m in lista}
                for m in lista + [m for m in (llm_disponibles or []) if m not in lista]],
        "llm_ajustes": {"razonamiento": llm.get("razonamiento", "none"), "clave": _mascara(buscar_clave())},
        "descarga": descarga,
    }


# ------------------------------------------------------------------ descargas
def descargar_whisper(m: Modelo, progreso: Callable[[float], None]):
    from huggingface_hub import snapshot_download
    from tqdm.auto import tqdm

    class Avance(tqdm):
        """Barra de huggingface_hub sin salida en pantalla: solo informa el avance en bytes."""

        def __init__(self, *args, **kwargs):
            # El servicio define TQDM_DISABLE=1; una barra desactivada no cuenta bytes.
            self._nulo = open(os.devnull, "w")
            kwargs.update(file=self._nulo, disable=False)
            super().__init__(*args, **kwargs)

        def update(self, n=1):
            r = super().update(n)
            if self.unit == "B" and self.total:
                progreso(min(0.99, self.n / self.total))
            return r

        def close(self):
            super().close()
            self._nulo.close()

    snapshot_download(m.id, tqdm_class=Avance)
    progreso(1.0)


def _bajar_archivo(url: str, destino: Path, progreso: Callable[[int], None]):
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_suffix(destino.suffix + ".parcial")
    with urllib.request.urlopen(url, timeout=30) as r, open(temporal, "wb") as f:
        while bloque := r.read(1 << 16):
            f.write(bloque)
            progreso(len(bloque))
    temporal.rename(destino)


def descargar_piper(m: Modelo, progreso: Callable[[float], None]):
    idioma, hablante, calidad = m.id.split("-", 2)
    base = f"{URL_PIPER}/{idioma}/{hablante}/{calidad}/{m.id}"
    hecho = 0

    def avance(n: int):
        nonlocal hecho
        hecho += n
        if m.bytes:
            progreso(min(0.99, hecho / m.bytes))

    _bajar_archivo(f"{base}.onnx.json", PIPER / f"{m.id}.onnx.json", avance)
    _bajar_archivo(f"{base}.onnx", PIPER / f"{m.id}.onnx", avance)
    progreso(1.0)


def borrar(tipo: str, id_: str):
    """Borra un modelo descargado (Whisper de la caché de Hugging Face o voz de Piper)."""
    if tipo == "stt":
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", id_):
            raise ValueError(f"modelo inválido: {id_}")
        shutil.rmtree(_carpeta_hf(id_))
    elif tipo == "voz" and re.fullmatch(r"[\w-]+", id_):
        for f in (PIPER / f"{id_}.onnx", PIPER / f"{id_}.onnx.json"):
            f.unlink(missing_ok=True)
    else:
        raise ValueError(f"no se puede borrar: {tipo} {id_}")


def buscar(tipo: str, id_: str) -> Modelo | None:
    lista = whisper_disponibles() if tipo == "stt" else piper_disponibles()
    return next((m for m in lista if m.id == id_), None)


# ------------------------------------------------------------ configuración
def _toml(valor) -> str:
    """Texto, número, booleano, lista o tabla en sintaxis TOML (JSON coincide en estos casos)."""
    if isinstance(valor, dict):
        return "{ " + ", ".join(f"{json.dumps(k)} = {_toml(v)}" for k, v in valor.items()) + " }"
    if isinstance(valor, list):
        return "[" + ", ".join(json.dumps(v, ensure_ascii=False) for v in valor) + "]"
    return json.dumps(valor, ensure_ascii=False)


def guardar_opcion(seccion: str, clave: str, valor):
    """Cambia `clave = valor` dentro de [seccion] en config.toml, sin tocar el resto."""
    ruta = CONFIG / "config.toml"
    lineas = ruta.read_text(encoding="utf-8").splitlines()
    nueva = f"{clave} = {_toml(valor)}"
    inicio, fin = None, len(lineas)
    for i, linea in enumerate(lineas):
        s = linea.strip()
        if s.startswith("["):
            if inicio is not None:
                fin = i
                break
            if s == f"[{seccion}]":
                inicio = i
    if inicio is None:
        lineas += ["", f"[{seccion}]", nueva]
    else:
        for i in range(inicio + 1, fin):
            if re.match(rf"\s*{re.escape(clave)}\s*=", lineas[i]):
                lineas[i] = nueva
                break
        else:
            lineas.insert(inicio + 1, nueva)
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def _sin_seccion(lineas: list[str], seccion: str) -> list[str]:
    """Quita [seccion] y sus líneas (hasta la siguiente sección)."""
    resultado, dentro = [], False
    for linea in lineas:
        s = linea.strip()
        if s.startswith("["):
            dentro = s == f"[{seccion}]"
        if not dentro:
            resultado.append(linea)
    while resultado and not resultado[-1].strip():
        resultado.pop()
    return resultado


def guardar_seccion(seccion: str, valores: dict):
    """Reemplaza [seccion] completa en config.toml. Si guarda claves (env, headers), deja el
    archivo legible solo por el usuario."""
    ruta = CONFIG / "config.toml"
    lineas = _sin_seccion(ruta.read_text(encoding="utf-8").splitlines(), seccion)
    lineas += ["", f"[{seccion}]"] + [f"{k} = {_toml(v)}" for k, v in valores.items()]
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    if any(isinstance(v, dict) and v for v in valores.values()):
        ruta.chmod(0o600)


def quitar_seccion(seccion: str):
    ruta = CONFIG / "config.toml"
    lineas = _sin_seccion(ruta.read_text(encoding="utf-8").splitlines(), seccion)
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
