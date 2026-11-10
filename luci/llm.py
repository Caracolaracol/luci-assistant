"""Cerebro: modelo de lenguaje vía OpenCode Go (API compatible con OpenAI)."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator

from openai import OpenAI


# Reglas técnicas de la conversación por voz (la personalidad está en persona.md).
REGLAS_VOZ = """
Contexto técnico:
- Estás siempre escuchando en una habitación donde el usuario también puede hablar con otras personas, por teléfono o consigo mismo. Recibes transcripciones automáticas de lo que se dice cerca de ti, solo cuando te llaman por tu nombre o justo después de que respondiste.
- Si la frase no va dirigida a ti (por ejemplo, el usuario le habla a otra persona, comenta algo que no es para ti o menciona tu nombre hablando de ti en tercera persona), responde exactamente [SILENCIO] y nada más.
- Si te llaman por tu nombre para hablarte, o la frase es una continuación natural de la conversación contigo, responde normalmente.
- Ante la duda: si te nombraron, responde; si no te nombraron y no parece seguir tu conversación, [SILENCIO].
""".strip()


class Cerebro:
    def __init__(self, base_url: str, clave: str, modelo: str, persona: str,
                 max_tokens: int = 400, memoria_turnos: int = 10,
                 razonamiento: str | None = "none"):
        # OpenCode pide identificar el cliente y enviar un id de sesión estable.
        self._cliente = OpenAI(
            base_url=base_url, api_key=clave, timeout=30, max_retries=1,
            default_headers={
                "User-Agent": "luci-asistente-voz/0.1",
                "x-opencode-session": f"luci-{uuid.uuid4()}",
            },
        )
        self.modelo = modelo
        self.persona = persona
        self.max_tokens = max_tokens
        self.memoria_turnos = memoria_turnos
        # Sin razonamiento previo la primera palabra llega varios segundos antes.
        self.extra = {"reasoning_effort": razonamiento} if razonamiento else {}
        self.historial: list[dict] = []

    def calentar(self):
        """Abre la conexión HTTPS en segundo plano para ahorrar el saludo TLS
        en la primera pregunta de la sesión."""
        def _ping():
            try:
                self._cliente.models.list()
            except Exception:
                pass
        threading.Thread(target=_ping, daemon=True).start()

    def olvidar(self):
        self.historial.clear()

    def responder(self, texto: str, cancelar: threading.Event) -> Iterator[str]:
        """Genera la respuesta por fragmentos. Si `cancelar` se activa, corta el
        flujo y guarda en el historial solo lo que alcanzó a decirse."""
        self.historial.append({"role": "user", "content": texto})
        mensajes = [{"role": "system", "content": f"{self.persona}\n\n{REGLAS_VOZ}"}] + self.historial[-2 * self.memoria_turnos:]
        dicho = ""
        flujo = self._cliente.chat.completions.create(
            model=self.modelo, messages=mensajes, stream=True, max_tokens=self.max_tokens,
            **self.extra,
        )
        try:
            for trozo in flujo:
                if cancelar.is_set():
                    break
                if trozo.choices and trozo.choices[0].delta.content:
                    delta = trozo.choices[0].delta.content
                    dicho += delta
                    yield delta
        finally:
            flujo.close()
            if cancelar.is_set():
                dicho = (dicho + " …[interrumpido]").strip()
            self.historial.append({"role": "assistant", "content": dicho or "…"})
