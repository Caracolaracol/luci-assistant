"""Entrada y salida de audio por un dispositivo concreto (la Scarlett)."""

from __future__ import annotations

import collections
import queue
import threading
import time

import numpy as np
import sounddevice as sd
import soxr

SR = 16_000            # frecuencia de trabajo para VAD y voz a texto
FRAME_MS = 32          # tamaño de cuadro para el VAD (512 muestras, lo que usa Silero)
FRAME = SR * FRAME_MS // 1000


def buscar_dispositivo(nombre: str, tipo: str) -> int:
    """Devuelve el índice del primer dispositivo cuyo nombre contiene `nombre`."""
    clave = "max_input_channels" if tipo == "input" else "max_output_channels"
    for i, d in enumerate(sd.query_devices()):
        if nombre.lower() in d["name"].lower() and d[clave] > 0:
            return i
    disponibles = ", ".join(d["name"] for d in sd.query_devices())
    raise RuntimeError(f"No encontré un dispositivo de {tipo} con '{nombre}'. Disponibles: {disponibles}")


class Microfono:
    """Captura continua; entrega cuadros de 30 ms, mono, int16 a 16 kHz."""

    def __init__(self, dispositivo: str, canal: int = 1):
        self.indice = buscar_dispositivo(dispositivo, "input")
        info = sd.query_devices(self.indice)
        self.sr_nativo = int(info["default_samplerate"])
        self.canales = max(1, min(int(info["max_input_channels"]), 2))
        self.canal = min(max(canal, 1), self.canales) - 1
        self._resampler = soxr.ResampleStream(self.sr_nativo, SR, 1, dtype="float32")
        self._pendiente = np.zeros(0, dtype=np.int16)
        self.nivel = 0.0          # volumen actual (0–1), para el medidor de la interfaz
        self.silenciado = False   # si es True, el audio se descarta
        # Instante desde el que llegan ceros exactos (macOS los entrega sin permiso de micrófono).
        self._ceros_desde: float | None = None
        self.cola: queue.Queue[np.ndarray] = queue.Queue()
        self._stream = sd.InputStream(
            device=self.indice, samplerate=self.sr_nativo, channels=self.canales,
            dtype="float32", blocksize=0, callback=self._callback,
        )

    def _callback(self, indata, frames, tiempo, status):
        mono = np.ascontiguousarray(indata[:, self.canal])
        if self.silenciado:
            self.nivel = 0.0
            return
        if not mono.any():
            if self._ceros_desde is None:
                self._ceros_desde = time.monotonic()
        else:
            self._ceros_desde = None
        self.nivel = 0.7 * self.nivel + 0.3 * min(1.0, float(np.sqrt(np.mean(mono ** 2))) * 8)
        r = self._resampler.resample_chunk(mono)
        datos = np.concatenate([self._pendiente, (np.clip(r, -1, 1) * 32767).astype(np.int16)])
        n = len(datos) // FRAME
        for k in range(n):
            self.cola.put(datos[k * FRAME:(k + 1) * FRAME])
        self._pendiente = datos[n * FRAME:]

    def iniciar(self):
        self._stream.start()

    def detener(self):
        self._stream.stop()

    def reabrir(self):
        """Cierra y vuelve a abrir el flujo de entrada."""
        try:
            self._stream.stop()
        finally:
            self._ceros_desde = None
            self._stream.start()

    @property
    def sin_senal(self) -> bool:
        """True si lleva más de 3 s entregando silencio digital absoluto."""
        return self._ceros_desde is not None and time.monotonic() - self._ceros_desde > 3

    def leer(self, timeout: float | None = None) -> np.ndarray | None:
        try:
            return self.cola.get(timeout=timeout)
        except queue.Empty:
            return None

    def vaciar(self):
        while not self.cola.empty():
            try:
                self.cola.get_nowait()
            except queue.Empty:
                break


class Reproductor:
    """Salida continua con cola de fragmentos; se puede cortar al instante."""

    def __init__(self, dispositivo: str):
        self.indice = buscar_dispositivo(dispositivo, "output")
        info = sd.query_devices(self.indice)
        self.sr = int(info["default_samplerate"])
        self._cola: collections.deque[np.ndarray] = collections.deque()
        self._actual = np.zeros(0, dtype=np.float32)
        self._lock = threading.Lock()
        self._stream = sd.OutputStream(
            device=self.indice, samplerate=self.sr, channels=2,
            dtype="float32", callback=self._callback,
        )

    def _callback(self, outdata, frames, tiempo, status):
        out = np.zeros(frames, dtype=np.float32)
        llenos = 0
        with self._lock:
            while llenos < frames:
                if len(self._actual) == 0:
                    if not self._cola:
                        break
                    self._actual = self._cola.popleft()
                n = min(frames - llenos, len(self._actual))
                out[llenos:llenos + n] = self._actual[:n]
                self._actual = self._actual[n:]
                llenos += n
        outdata[:, 0] = out
        outdata[:, 1] = out

    def iniciar(self):
        self._stream.start()

    def detener(self):
        self._stream.stop()

    def encolar(self, audio: np.ndarray, sr: int):
        audio = audio.astype(np.float32)
        if sr != self.sr:
            audio = soxr.resample(audio, sr, self.sr)
        with self._lock:
            self._cola.append(audio)

    def cortar(self):
        with self._lock:
            self._cola.clear()
            self._actual = np.zeros(0, dtype=np.float32)

    @property
    def sonando(self) -> bool:
        with self._lock:
            return bool(self._cola) or len(self._actual) > 0

    def pitido(self, frecuencias=(880,), duracion=0.09, volumen=0.15):
        """Señal corta: tonos ascendentes o descendentes según `frecuencias`."""
        partes = []
        for f in frecuencias:
            t = np.arange(int(self.sr * duracion)) / self.sr
            env = np.minimum(1, np.minimum(t, t[::-1]) / 0.01)
            partes.append(np.sin(2 * np.pi * f * t) * env * volumen)
        self.encolar(np.concatenate(partes), self.sr)
