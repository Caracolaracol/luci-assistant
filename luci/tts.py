"""Texto a voz local: Piper (neural) o las voces de macOS (`say`)."""

from __future__ import annotations

import re
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np


class VozPiper:
    def __init__(self, ruta_modelo: Path, lentitud: float = 1.0):
        from piper import PiperVoice, SynthesisConfig

        self._voz = PiperVoice.load(str(ruta_modelo))
        self._cfg = SynthesisConfig(length_scale=lentitud)

    def sintetizar(self, texto: str) -> tuple[np.ndarray, int]:
        trozos = list(self._voz.synthesize(texto, syn_config=self._cfg))
        if not trozos:
            return np.zeros(0, dtype=np.float32), 22050
        return np.concatenate([t.audio_float_array for t in trozos]), trozos[0].sample_rate


class VozSay:
    def __init__(self, voz: str = "Paulina"):
        self.voz = voz

    def sintetizar(self, texto: str) -> tuple[np.ndarray, int]:
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            subprocess.run(["say", "-v", self.voz, "-o", f.name, "--data-format=LEI16@22050", texto],
                           check=True)
            with wave.open(f.name) as w:
                datos = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
                return datos.astype(np.float32) / 32768.0, w.getframerate()


def crear_voz(cfg: dict, ruta_datos) -> VozPiper | VozSay:
    if cfg.get("motor", "piper") == "say":
        return VozSay(cfg.get("voz_say", "Paulina"))
    return VozPiper(ruta_datos(cfg["voz_piper"]), cfg.get("lentitud_piper", 1.0))


# Fin de frase: puntuación seguida de espacio (evita cortar "3.5" o "etc." a mitad de flujo).
_FIN_FRASE = re.compile(r"[.!?;:…]+[\"'»)\]]*\s+")


def limpiar_para_voz(texto: str) -> str:
    """Quita markdown, código, rutas y direcciones web: no se deben pronunciar."""
    texto = re.sub(r"```.*?(```|$)", " ", texto, flags=re.S)          # bloques de código
    texto = re.sub(r"`[^`\n]*`", " ", texto)                          # código en línea
    texto = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", texto)              # enlaces markdown
    texto = re.sub(r"(https?://|www\.)\S*[\w/]", " ", texto)            # direcciones web
    texto = re.sub(r"(?<![\w.])(~|\.{1,2})?(/[\w.@-]*[\w@-]){2,}/?", " ", texto)   # rutas de archivo
    texto = re.sub(r"[*_#`>|~]+", "", texto)
    texto = re.sub(r"^\s*[-•]\s+", "", texto, flags=re.M)
    texto = re.sub(r"\s+", " ", texto)
    texto = re.sub(r"\s+([,.;:!?])", r"\1", texto)
    return re.sub(r"([,;:])(?=[,.;:!?])", "", texto).strip()


class Fraseador:
    """Acumula texto en streaming y entrega frases completas para sintetizar.

    La primera frase sale apenas termina (menos latencia); las siguientes se
    agrupan hasta `min_chars` para que la voz suene más fluida.
    """

    def __init__(self, min_chars: int = 40):
        self._buffer = ""
        self._emitidas = 0
        self.min_chars = min_chars

    def agregar(self, delta: str) -> list[str]:
        self._buffer += delta
        salida = []
        while True:
            corte = None
            for m in _FIN_FRASE.finditer(self._buffer):
                if self._emitidas == 0 or m.end() >= self.min_chars:
                    corte = m.end()
                    break
            if corte is None:
                break
            frase = limpiar_para_voz(self._buffer[:corte])
            self._buffer = self._buffer[corte:]
            if frase:
                salida.append(frase)
                self._emitidas += 1
        return salida

    def terminar(self) -> list[str]:
        resto, self._buffer = limpiar_para_voz(self._buffer), ""
        return [resto] if resto else []
