"""Voz a texto local con Whisper (MLX, acelerado en Apple Silicon)."""

from __future__ import annotations

import threading

import numpy as np
from tqdm import tqdm

# mlx-whisper usa tqdm, que por defecto crea un candado de multiprocessing y lanza
# un proceso auxiliar; dentro de la interfaz de terminal eso falla. Un candado de
# hilos basta.
tqdm.set_lock(threading.RLock())


class Oido:
    def __init__(self, modelo: str, idioma: str = "es", pista: str | None = None):
        import mlx_whisper  # import diferido: tarda en cargar

        self._mlx = mlx_whisper
        self.modelo = modelo
        # Si el modelo ya está descargado se usa la copia local, sin consultar internet
        # en cada arranque (eso puede tardar varios segundos).
        try:
            from huggingface_hub import snapshot_download

            self._ruta = snapshot_download(modelo, local_files_only=True)
        except Exception:
            self._ruta = modelo
        self.idioma = idioma
        # Texto de contexto: ayuda a que Whisper escriba bien el nombre "Luci".
        self.pista = pista

    def calentar(self):
        """Descarga (la primera vez) y carga el modelo con un audio vacío."""
        self.transcribir(np.zeros(16_000, dtype=np.int16))

    def transcribir(self, audio: np.ndarray) -> str:
        datos = audio.astype(np.float32) / 32768.0
        # Un poco de silencio alrededor ayuda a Whisper con frases muy cortas («Luci»).
        margen = np.zeros(4800, dtype=np.float32)
        datos = np.concatenate([margen, datos, margen])
        r = self._mlx.transcribe(
            datos,
            path_or_hf_repo=self._ruta,
            language=self.idioma,
            # Una sola pasada: sin reintentos a mayor temperatura, que con ruido
            # pueden tardar varios segundos y suelen inventar texto.
            temperature=0.0,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
            initial_prompt=self.pista,
            verbose=None,
        )
        segmentos = [s for s in r.get("segments", []) if not _sospechoso(s)]
        texto = limpiar(" ".join(s["text"].strip() for s in segmentos))
        # Con ruido, Whisper a veces repite la pista en vez de transcribir
        # (solo frases de 3+ palabras: «Luci» a secas es válido).
        limpio = texto.lower().strip(" .,")
        if self.pista and len(limpio.split()) >= 3 and limpio in self.pista.lower():
            return ""
        return texto


def _sospechoso(seg: dict) -> bool:
    """Segmentos que Whisper genera a partir de ruido o silencio."""
    if seg.get("no_speech_prob", 0) > 0.6 and seg.get("avg_logprob", 0) < -0.7:
        return True
    if seg.get("compression_ratio", 0) > 2.4:          # texto repetitivo en bucle
        return True
    return seg.get("avg_logprob", 0) < -1.2              # muy poca confianza


# Frases que Whisper suele "alucinar" con silencio o ruido.
_ALUCINACIONES = {
    "gracias", "muchas gracias", "gracias por ver", "gracias por ver el video",
    "subtítulos realizados por la comunidad de amaraorg", "suscríbete", "y", "eh", "mmm", "ah",
    "amén",
}


def limpiar(texto: str) -> str:
    texto = texto.strip()
    clave = "".join(ch for ch in texto.lower() if ch.isalnum() or ch == " ").strip()
    if not clave or clave in _ALUCINACIONES or set(clave) <= set(". "):
        return ""
    return texto
