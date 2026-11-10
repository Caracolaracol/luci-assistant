"""Detección del fin de turno con Smart Turn v3 (Pipecat, licencia BSD-2-Clause).

En vez de esperar un silencio largo, ante una pausa breve se pregunta a un modelo
pequeño si la frase suena terminada (entonación, ritmo, alargamientos al dudar).
https://huggingface.co/pipecat-ai/smart-turn-v3
"""

from __future__ import annotations

import numpy as np
import onnxruntime as ort

from .config import DATOS

SR = 16_000
MUESTRAS = 8 * SR          # el modelo mira los últimos 8 s


class DetectorTurno:
    def __init__(self, ruta=DATOS / "models/smart-turn/smart-turn-v3.2-cpu.onnx"):
        import mlx_whisper.audio as audio_whisper

        self._mel = audio_whisper.log_mel_spectrogram
        opciones = ort.SessionOptions()
        opciones.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        opciones.inter_op_num_threads = 1
        opciones.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._sesion = ort.InferenceSession(str(ruta), sess_options=opciones,
                                            providers=["CPUExecutionProvider"])

    def _caracteristicas(self, audio: np.ndarray) -> np.ndarray:
        """Igual que la referencia: últimos 8 s, ceros al inicio, normalización a media 0
        y varianza 1, y espectrograma log-mel de Whisper (80 bandas × 800 cuadros)."""
        x = audio.astype(np.float32) / 32768.0
        x = x[-MUESTRAS:]
        if len(x) < MUESTRAS:
            x = np.pad(x, (MUESTRAS - len(x), 0))
        x = (x - x.mean()) / np.sqrt(x.var() + 1e-7)
        mel = np.array(self._mel(x, n_mels=80))          # (cuadros, 80)
        return mel.T[None, :, :800].astype(np.float32)   # (1, 80, 800)

    def probabilidad_fin(self, audio: np.ndarray) -> float:
        """Probabilidad (0–1) de que la frase haya terminado. `audio`: int16 a 16 kHz."""
        salida = float(self._sesion.run(None, {"input_features": self._caracteristicas(audio)})[0][0][0])
        if not 0.0 <= salida <= 1.0:                     # por si el modelo devolviera logits
            salida = 1.0 / (1.0 + np.exp(-salida))
        return salida
