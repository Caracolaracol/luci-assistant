"""Detección de voz: separa frases del flujo del micrófono."""

from __future__ import annotations

import collections
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field

import numpy as np
import onnxruntime as ort

from .audio import FRAME_MS, SR, Microfono
from .config import DATOS


def rms(cuadro: np.ndarray) -> float:
    return float(np.sqrt(np.mean(cuadro.astype(np.float32) ** 2)))


class DetectorVoz:
    """Silero VAD (red neuronal pequeña, local). Recibe cuadros de 512 muestras a 16 kHz."""

    _CONTEXTO = 64

    def __init__(self, umbral: float = 0.5, ruta=DATOS / "models/silero/silero_vad.onnx"):
        opciones = ort.SessionOptions()
        opciones.intra_op_num_threads = 1
        opciones.inter_op_num_threads = 1
        self._sesion = ort.InferenceSession(str(ruta), sess_options=opciones,
                                            providers=["CPUExecutionProvider"])
        self._sr = np.array(SR, dtype=np.int64)
        self.umbral = umbral
        self.reiniciar()

    def reiniciar(self):
        self._estado = np.zeros((2, 1, 128), dtype=np.float32)
        self._contexto = np.zeros(self._CONTEXTO, dtype=np.float32)

    def probabilidad(self, cuadro: np.ndarray) -> float:
        x = cuadro.astype(np.float32) / 32768.0
        entrada = np.concatenate([self._contexto, x])[None, :]
        salida, self._estado = self._sesion.run(
            None, {"input": entrada, "state": self._estado, "sr": self._sr})
        self._contexto = x[-self._CONTEXTO:]
        return float(salida[0][0])

    def es_voz(self, cuadro: np.ndarray) -> bool:
        return self.probabilidad(cuadro) >= self.umbral


@dataclass
class Frase:
    audio: np.ndarray
    cuadros_voz: int                 # cuadros de 30 ms clasificados como voz
    volumen_voz: float               # RMS medio de los cuadros con voz
    transcripcion: Future | None = field(default=None)   # transcripción anticipada

    @property
    def duracion_voz_ms(self) -> int:
        return self.cuadros_voz * FRAME_MS

    @property
    def proporcion_voz(self) -> float:
        return self.cuadros_voz * FRAME / max(1, len(self.audio))


FRAME = SR * FRAME_MS // 1000


def escuchar_frase(
    mic: Microfono,
    vad: DetectorVoz,
    espera_s: float,
    silencio_fin_ms: int = 500,
    max_frase_s: float = 20,
    inicio: list[np.ndarray] | None = None,
    anticipar: Callable[[np.ndarray], Future] | None = None,
    anticipar_ms: int = 150,
    parar: Callable[[], bool] | None = None,
) -> Frase | None:
    """Espera a que el usuario hable y devuelve la frase completa (int16, 16 kHz).

    - `inicio` continúa una frase ya empezada (por ejemplo, tras una interrupción).
    - `anticipar`, si se indica, se llama al detectar una pausa de `anticipar_ms`
      con el audio hasta ese momento (para empezar a transcribir mientras se
      confirma que la frase terminó). Si el usuario sigue hablando, se descarta.

    - `parar`, si se indica, se consulta mientras nadie habla; si devuelve True,
      la escucha termina sin frase.

    Devuelve None si nadie habla durante `espera_s` segundos (None = sin límite).
    """
    previo = collections.deque(maxlen=600 // FRAME_MS)   # 600 ms antes de la voz (Silero confirma con retraso)
    arranque = collections.deque(maxlen=4)               # ventana para confirmar inicio
    frase: list[np.ndarray] = list(inicio or [])
    hablando = bool(frase)
    cuadros_voz = len(frase)
    suma_rms = sum(rms(c) for c in frase)
    silencio = 0
    esperados = int(espera_s * 1000 / FRAME_MS) if espera_s else None
    max_cuadros = int(max_frase_s * 1000 / FRAME_MS)
    fin = max(1, silencio_fin_ms // FRAME_MS)
    pausa_anticipo = max(1, anticipar_ms // FRAME_MS)
    cuadros_sin_voz = 0
    futuro: Future | None = None
    largo_anticipado = 0

    def cerrar() -> Frase:
        corte = len(frase) - max(0, silencio - 5)          # deja ~150 ms de margen
        audio = np.concatenate(frase[:corte])
        # la transcripción anticipada solo vale si se hizo con el mismo audio útil
        valido = futuro if futuro is not None and largo_anticipado >= corte else None
        if futuro is not None and valido is None:
            futuro.cancel()
        return Frase(audio, cuadros_voz, suma_rms / max(1, cuadros_voz), valido)

    while True:
        if not hablando and parar is not None and parar():
            return None
        cuadro = mic.leer(timeout=0.2)
        if cuadro is None:
            continue
        voz = vad.es_voz(cuadro)
        if not hablando:
            previo.append(cuadro)
            arranque.append(voz)
            if sum(arranque) >= 3:          # ~100 ms de voz en 130 ms
                hablando = True
                frase = list(previo)
                cuadros_voz = sum(arranque)
                suma_rms = sum(rms(c) for c in list(previo)[-len(arranque):])
                silencio = 0
                continue
            cuadros_sin_voz += 1
            if esperados is not None and cuadros_sin_voz >= esperados:
                return None
            continue

        frase.append(cuadro)
        if voz:
            cuadros_voz += 1
            suma_rms += rms(cuadro)
            silencio = 0
            if futuro is not None:              # siguió hablando: se descarta el anticipo
                futuro.cancel()
                futuro = None
        else:
            silencio += 1
            if anticipar and futuro is None and silencio == pausa_anticipo:
                largo_anticipado = len(frase)
                futuro = anticipar(np.concatenate(frase))
        if silencio >= fin or len(frase) >= max_cuadros:
            return cerrar()
