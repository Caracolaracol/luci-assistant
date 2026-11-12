"""Cerebro: modelo de lenguaje vía OpenCode Go (API compatible con OpenAI)."""

from __future__ import annotations

import itertools
import re
import threading
import uuid
from collections.abc import Callable, Iterator

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

# Errores en que conviene probar con otro modelo: servicio saturado (429), caído (5xx),
# sin respuesta a tiempo o sin conexión. Una clave inválida (401) no se arregla así.
_REINTENTABLES = (APIConnectionError, APITimeoutError)


def _reintentable(e: Exception) -> bool:
    if isinstance(e, _REINTENTABLES):
        return True
    return isinstance(e, APIStatusError) and (e.status_code == 429 or e.status_code >= 500)


# Reglas técnicas de la conversación por voz (la personalidad está en persona.md).
REGLAS_VOZ = """
Contexto técnico:
- Estás siempre escuchando en una habitación donde el usuario también puede hablar con otras personas, por teléfono o consigo mismo. Recibes transcripciones automáticas de lo que se dice cerca de ti, solo cuando te llaman por tu nombre o justo después de que respondiste.
- Si la frase no va dirigida a ti (por ejemplo, el usuario le habla a otra persona, comenta algo que no es para ti o menciona tu nombre hablando de ti en tercera persona), responde exactamente [SILENCIO] y nada más.
- Si te llaman por tu nombre para hablarte, o la frase es una continuación natural de la conversación contigo, responde normalmente.
- Ante la duda: si te nombraron, responde; si no te nombraron y no parece seguir tu conversación, [SILENCIO].
""".strip()


# Se agrega al prompt de sistema cuando hay herramientas (MCP) activas.
REGLAS_HERRAMIENTAS = """
Herramientas:
- Tienes herramientas para consultar información del usuario:
{lista}
- Úsalas solo cuando la pregunta lo necesite. Antes de usarlas di una frase muy breve, como «Déjame revisar.», para que el usuario sepa que estás buscando.
- Lo que devuelven son notas técnicas escritas por asistentes de programación: tienen rutas, nombres de archivos y variables, identificadores, comandos, commits y jerga. NUNCA leas eso en voz alta. Entiende la nota y cuenta lo que significa en lenguaje cotidiano, como se lo contarías a un amigo.
- Responde la pregunta en dos o tres frases, con lo esencial. Sin listas, sin códigos ni números de referencia, sin nombres técnicos, salvo que el usuario los pida. Si quiere más detalle, te lo pedirá.
- Si no encuentras nada útil, dilo en una frase.
""".strip()

MAX_RONDAS = 3      # consultas a herramientas por respuesta, como máximo

# Formato interno con que algunos modelos escriben una llamada a herramienta como texto
# (DeepSeek: <｜DSML｜…>, otros: <tool_call>, <|tool…|>). Nunca se dice en voz alta.
_FORMATO_INTERNO = re.compile(r"<[｜|]|DSML|<tool_call|<function[=_]")


def listar_modelos(base_url: str, clave: str) -> list[str]:
    """Modelos que ofrece el proveedor (OpenCode Go)."""
    cliente = OpenAI(base_url=base_url, api_key=clave, timeout=10, max_retries=0,
                     default_headers={"User-Agent": "luci-asistente-voz/0.2"})
    return [m.id for m in cliente.models.list().data]


class Cerebro:
    def __init__(self, base_url: str, clave: str, modelo: str, persona: str,
                 max_tokens: int = 400, memoria_turnos: int = 10,
                 razonamiento: str | None = "none", respaldo: list[str] | None = None,
                 herramientas=None):
        self.base_url = base_url
        self.cambiar_clave(clave)
        self.modelo = modelo
        self.persona = persona
        self.max_tokens = max_tokens
        self.memoria_turnos = memoria_turnos
        self.razonamiento = razonamiento
        self.historial: list[dict] = []
        self.respaldo = respaldo or []
        self.herramientas = herramientas   # luci.mcp.Herramientas o None
        self.usado = modelo          # modelo que respondió la última vez
        self.fallos: list[str] = []  # modelos que fallaron en la última respuesta

    @property
    def extra(self) -> dict:
        # Sin razonamiento previo la primera palabra llega varios segundos antes.
        return {"reasoning_effort": self.razonamiento} if self.razonamiento else {}

    def cambiar_clave(self, clave: str):
        # OpenCode pide identificar el cliente y enviar un id de sesión estable.
        # Para voz no sirve esperar 30 s: si el modelo no empieza a responder en ~12 s,
        # se pasa al siguiente modelo de respaldo.
        self._cliente = OpenAI(
            base_url=self.base_url, api_key=clave, max_retries=0,
            timeout=12.0,
            default_headers={
                "User-Agent": "luci-asistente-voz/0.2",
                "x-opencode-session": f"luci-{uuid.uuid4()}",
            },
        )

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

    def descartar_ultimo_turno(self):
        """Quita la última pregunta (y su respuesta, si la hay) del historial."""
        if self.historial and self.historial[-1]["role"] == "assistant":
            self.historial.pop()
        if self.historial and self.historial[-1]["role"] == "user":
            self.historial.pop()

    def _sistema(self) -> str:
        texto = f"{self.persona}\n\n{REGLAS_VOZ}"
        if self.herramientas and self.herramientas.esquemas():
            texto += "\n\n" + REGLAS_HERRAMIENTAS.format(
                lista=self.herramientas.instrucciones() or "- (sin descripción)")
        return texto

    def responder(self, texto: str, cancelar: threading.Event,
                  al_usar: Callable[[str, str], None] | None = None) -> Iterator[str]:
        """Genera la respuesta por fragmentos. Si `cancelar` se activa, corta el
        flujo y guarda en el historial solo lo que alcanzó a decirse.

        Si el modelo pide usar herramientas (MCP), las ejecuta, le entrega los resultados y
        sigue generando. Los resultados no se guardan en el historial: si hace falta, el
        modelo vuelve a consultar en la pregunta siguiente."""
        self.historial.append({"role": "user", "content": texto})
        mensajes = [{"role": "system", "content": self._sistema()}] + self.historial[-2 * self.memoria_turnos:]
        esquemas = self.herramientas.esquemas() if self.herramientas else []
        resultados: list[str] = []      # lo que devolvieron las herramientas, para la ronda final
        dicho = ""
        try:
            for ronda in range(MAX_RONDAS + 1):
                final = not esquemas or ronda == MAX_RONDAS
                if final and resultados:
                    # Respuesta final sin herramientas: los resultados van como texto. Si se
                    # le prohibiera usarlas con tool_choice, algunos modelos (DeepSeek)
                    # escriben la llamada como texto con su formato interno.
                    pedido = mensajes[:1 + len(self.historial[-2 * self.memoria_turnos:])] + [{
                        "role": "system",
                        "content": "Ya consultaste las herramientas; no puedes consultar más. Lo que "
                                   "encontraste:\n\n" + "\n\n".join(resultados)[:12000] +
                                   "\n\nResponde ahora la última pregunta del usuario en lenguaje "
                                   "hablado, siguiendo las reglas.",
                    }]
                    flujo, trozos = self._abrir_flujo(pedido)
                elif final:
                    flujo, trozos = self._abrir_flujo(mensajes)
                else:
                    extra = {"tools": esquemas, "tool_choice": "auto"}
                    flujo, trozos = self._abrir_con_herramientas(mensajes, extra)
                    if not extra:
                        esquemas = []      # el modelo no acepta herramientas
                llamadas: dict[int, dict] = {}
                texto_ronda, retenido, formato_interno = "", "", False
                try:
                    for trozo in trozos:
                        if cancelar.is_set():
                            break
                        if not trozo.choices:
                            continue
                        d = trozo.choices[0].delta
                        if d.content:
                            retenido += d.content
                            if _FORMATO_INTERNO.search(retenido):
                                formato_interno = True      # llamada escrita como texto: no se dice
                                break
                            if retenido.endswith("<"):
                                continue                    # puede ser el inicio de ese formato
                            texto_ronda += retenido
                            dicho += retenido
                            yield retenido
                            retenido = ""
                        for tc in d.tool_calls or []:
                            c = llamadas.setdefault(tc.index or 0, {"id": "", "nombre": "", "args": ""})
                            c["id"] = tc.id or c["id"]
                            if tc.function:
                                c["nombre"] += tc.function.name or ""
                                c["args"] += tc.function.arguments or ""
                finally:
                    flujo.close()
                if retenido and not formato_interno and not cancelar.is_set():
                    dicho += retenido
                    yield retenido
                if cancelar.is_set() or final or (not llamadas and not formato_interno):
                    break
                if formato_interno and not llamadas:
                    esquemas = []          # la siguiente ronda es la final, sin herramientas
                    continue
                pedidas = [{"id": c["id"] or f"llamada_{ronda}_{i}", "type": "function",
                            "function": {"name": c["nombre"], "arguments": c["args"] or "{}"}}
                           for i, c in sorted(llamadas.items())]
                mensajes.append({"role": "assistant", "content": texto_ronda or None, "tool_calls": pedidas})
                for p in pedidas:
                    if al_usar:
                        al_usar(p["function"]["name"], p["function"]["arguments"])
                    resultado = self.herramientas.llamar(p["function"]["name"], p["function"]["arguments"])
                    mensajes.append({"role": "tool", "tool_call_id": p["id"], "content": resultado or "(vacío)"})
                    resultados.append(resultado or "(vacío)")
                if cancelar.is_set():
                    break
                if dicho and not dicho.endswith((" ", "\n")):
                    dicho += " "
                    yield " "
        finally:
            if cancelar.is_set():
                dicho = (dicho + " …[interrumpido]").strip()
            self.historial.append({"role": "assistant", "content": dicho.strip() or "…"})

    def _abrir_con_herramientas(self, mensajes: list[dict], extra: dict):
        """Como _abrir_flujo, pero si el modelo rechaza las herramientas (400), reintenta
        sin ellas. Vacía `extra` en ese caso."""
        try:
            return self._abrir_flujo(mensajes, **extra)
        except APIStatusError as e:
            if not extra or e.status_code != 400:
                raise
            extra.clear()
            return self._abrir_flujo(mensajes)

    def _abrir_flujo(self, mensajes: list[dict], **opciones):
        """Pide la respuesta al modelo elegido y, si está saturado o no responde, a los de
        respaldo. Devuelve el flujo (para cerrarlo) y sus fragmentos, el primero ya recibido."""
        self.fallos = []
        orden = [self.modelo] + [m for m in self.respaldo if m != self.modelo]
        for i, modelo in enumerate(orden):
            try:
                flujo = self._cliente.chat.completions.create(
                    model=modelo, messages=mensajes, stream=True, max_tokens=self.max_tokens,
                    **self.extra, **opciones,
                )
                resto = iter(flujo)
                primero = next(resto, None)
                self.usado = modelo
                return flujo, itertools.chain([primero] if primero is not None else [], resto)
            except Exception as e:
                if not _reintentable(e) or i == len(orden) - 1:
                    raise
                self.fallos.append(f"{modelo} ({getattr(e, 'status_code', None) or type(e).__name__})")
        raise RuntimeError("sin modelos")
