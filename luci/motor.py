"""Motor de Luci: escucha siempre y decide cuándo hablar.

Estados:
  espera      escucha y transcribe en local, pero solo responde si oye su nombre.
  conversando ventana abierta tras una respuesta: acepta continuaciones sin el
              nombre; el modelo descarta lo que no va dirigido a Luci.
  pensando    esperando al modelo.
  hablando    reproduciendo la respuesta (se puede interrumpir).
  silenciado  micrófono desactivado por el usuario.

Nada sale del Mac mientras Luci está en espera: solo se envía texto al modelo
cuando la llaman por su nombre o durante la ventana de conversación.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np

from .audio import FRAME_MS, Microfono, Reproductor
from .config import cargar_persona, clave_opencode_go, ruta_datos
from .llm import Cerebro
from .stt import Oido
from .tts import Fraseador, crear_voz
from .vad import DetectorVoz, Frase, escuchar_frase, rms

from .estados import CARGANDO, CONVERSANDO, ESPERA, HABLANDO, PENSANDO, SILENCIADO  # noqa: E402

# Variantes con que Whisper suele escribir el nombre.
# Luci, Lucy, Lusi, Luzi, Lucí… pero no «Lucía» ni «luz».
_NOMBRE = re.compile(r"\blu[csz][iíy]\b", re.I)
_RELLENO = re.compile(r"\b(?:oye|hola|hey|ey|eh|ok|okay|buenas|bueno)\b", re.I)
_CIERRE = re.compile(
    r"\b(?:gracias|silencio|c[aá]llate|eso es todo|nada m[aá]s|adi[oó]s|hasta luego|chao|descansa)\b", re.I)
_SILENCIO = "[SILENCIO]"


@dataclass
class Evento:
    tipo: str        # "usuario" | "luci" | "info" | "debug" | "error"
    texto: str


def _palabras(t: str) -> set[str]:
    return set(re.findall(r"\w+", t.lower()))


class Motor:
    def __init__(self, cfg: dict, emitir: Callable[[Evento], None]):
        self.cfg = cfg
        self._emitir = emitir
        self.estado = CARGANDO
        self.fin_ventana = 0.0          # hora en que se cierra la ventana de conversación
        self._activo = False
        self._ultimo_dicho = ""         # para reconocer el eco de su propia voz

    # ------------------------------------------------------------- utilidades
    def _ev(self, tipo: str, texto: str):
        self._emitir(Evento(tipo, texto))

    @property
    def nivel(self) -> float:
        return self.mic.nivel if hasattr(self, "mic") else 0.0

    @property
    def modelo(self) -> str:
        return self.cerebro.modelo if hasattr(self, "cerebro") else self.cfg["llm"]["modelo"]

    def cargar(self):
        a = self.cfg["audio"]
        self._ev("info", "Preparando audio…")
        self.mic = Microfono(a["dispositivo"], a.get("canal_microfono", 1))
        self.salida = Reproductor(a["dispositivo"])
        self.vad = DetectorVoz(self.cfg["vad"].get("umbral", 0.5))
        self._ev("info", "Cargando voz…")
        self.voz = crear_voz(self.cfg["tts"], ruta_datos)
        self._ev("info", f"Cargando Whisper ({self.cfg['stt']['modelo'].split('/')[-1]})…")
        self.oido = Oido(self.cfg["stt"]["modelo"], self.cfg["stt"]["idioma"], self.cfg["stt"].get("pista"))
        self.oido.calentar()
        self._hilo_stt = ThreadPoolExecutor(max_workers=1)
        l = self.cfg["llm"]
        self.cerebro = Cerebro(l["base_url"], clave_opencode_go(), l["modelo"], cargar_persona(),
                               l.get("max_tokens", 400), l.get("memoria_turnos", 10),
                               l.get("razonamiento", "none"))
        self.cerebro.calentar()

    # --------------------------------------------------------------- control
    def silenciar(self, si: bool):
        self.mic.silenciado = si
        if si:
            self.salida.cortar()
            self.estado = SILENCIADO
            self._ev("info", "Micrófono silenciado.")
        else:
            self.mic.vaciar()
            self.estado = ESPERA
            self._ev("info", "Micrófono activo. Llámame por mi nombre.")

    def abrir_conversacion(self):
        """Equivale a llamarla por su nombre (atajo de teclado)."""
        if self.estado in (ESPERA, CONVERSANDO):
            self._abrir_ventana()
            self.salida.pitido((660, 990))
            self.cerebro.calentar()

    def nueva_conversacion(self):
        self.cerebro.olvidar()
        self._ev("info", "Memoria de la conversación borrada.")

    def cambiar_modelo(self, modelo: str):
        self.cerebro.modelo = modelo
        self._ev("info", f"Modelo: {modelo}")

    def detener(self):
        self._activo = False

    def _abrir_ventana(self):
        self.fin_ventana = time.time() + self.cfg["sesion"].get("ventana_s", 6)
        self.estado = CONVERSANDO

    def _cerrar_ventana(self, sonido: bool = True):
        self.fin_ventana = 0.0
        if self.estado != SILENCIADO:
            self.estado = ESPERA
        if sonido:
            self.salida.pitido((990, 660), volumen=0.08)

    # ----------------------------------------------------------- bucle principal
    def ejecutar(self):
        self.cargar()
        self.mic.iniciar()
        self.salida.iniciar()
        self._activo = True
        self.estado = ESPERA
        threading.Thread(target=self._vigilar_microfono, daemon=True).start()
        self._ev("info", "Listo. Llámame por mi nombre: «Luci, …».")
        inicio = None
        v = self.cfg["vad"]
        try:
            while self._activo:
                if self.mic.silenciado:
                    time.sleep(0.1)
                    continue
                if self.estado == CONVERSANDO and time.time() > self.fin_ventana:
                    self._cerrar_ventana()
                    self._ev("debug", "Ventana de conversación cerrada.")
                # Escucha continua: solo se corta para cerrar la ventana, silenciar o salir.
                frase = escuchar_frase(
                    self.mic, self.vad, espera_s=None, silencio_fin_ms=v["silencio_fin_ms"],
                    max_frase_s=v["max_frase_s"], inicio=inicio,
                    anticipar=self._anticipar if v.get("anticipar", True) else None,
                    parar=self._debe_parar,
                )
                inicio = None
                if frase is None or self.mic.silenciado:
                    continue
                inicio = self._procesar(frase)
        finally:
            self.mic.detener()
            self.salida.detener()

    def _vigilar_microfono(self):
        """Avisa (una vez) si el micrófono entrega silencio absoluto."""
        avisado = False
        ultimo_reinicio = time.monotonic()
        while self._activo:
            if self.mic.sin_senal and not self.mic.silenciado:
                if not avisado:
                    self._ev("error", "El micrófono entrega silencio absoluto (ceros): macOS no le da "
                                      "permiso de micrófono a Luci. Acepta el aviso de macOS o actívalo en "
                                      "Ajustes del Sistema › Privacidad y seguridad › Micrófono.")
                    avisado = True
                # Reabre el micrófono cada 10 s: así empieza a oír en cuanto se concede el permiso.
                if time.monotonic() - ultimo_reinicio > 10:
                    self.mic.reabrir()
                    ultimo_reinicio = time.monotonic()
            elif avisado:
                self._ev("info", "El micrófono vuelve a tener señal.")
                avisado = False
            time.sleep(1)

    def _debe_parar(self) -> bool:
        return (not self._activo or self.mic.silenciado
                or (self.estado == CONVERSANDO and time.time() > self.fin_ventana))

    def _anticipar(self, audio: np.ndarray):
        return self._hilo_stt.submit(self.oido.transcribir, audio)

    def _descartar(self, frase: Frase) -> str | None:
        f = self.cfg.get("filtro", {})
        if frase.duracion_voz_ms < f.get("min_voz_ms", 250):
            return f"muy corta ({frase.duracion_voz_ms} ms de voz)"
        if frase.proporcion_voz < f.get("min_proporcion_voz", 0.3):
            return f"poca voz ({frase.proporcion_voz:.0%} del audio)"
        if frase.volumen_voz < f.get("volumen_min", 150):
            return f"muy baja (volumen {frase.volumen_voz:.0f})"
        return None

    def _es_eco(self, texto: str) -> bool:
        """¿Es su propia voz captada por el micrófono?"""
        dicho, oido = _palabras(self._ultimo_dicho), _palabras(texto)
        # solo frases de 3+ palabras: un "¿Júpiter?" del usuario no es eco
        return len(oido) >= 3 and len(oido & dicho) / len(oido) > 0.7

    def _procesar(self, frase: Frase) -> list[np.ndarray] | None:
        t_fin = time.time() - self.cfg["vad"]["silencio_fin_ms"] / 1000
        motivo = self._descartar(frase)
        if motivo:
            if frase.transcripcion:
                frase.transcripcion.cancel()
            self._ev("debug", f"Ignorado: {motivo}")
            return None
        texto = (frase.transcripcion.result() if frase.transcripcion is not None
                 else self.oido.transcribir(frase.audio))
        if not texto:
            self._ev("debug", "Ignorado: Whisper no reconoció palabras")
            return None
        if self._es_eco(texto):
            self._ev("debug", f"Ignorado (eco de mi voz): {texto}")
            return None

        nombrado = bool(_NOMBRE.search(texto))
        en_ventana = self.estado == CONVERSANDO

        if not nombrado and not en_ventana:
            self._ev("debug", f"Oído en espera (no me llamaron): {texto}")
            return None

        self._ev("usuario", texto)

        # Solo el nombre ("Luci", "Oye Luci"): confirma que escucha.
        resto = _RELLENO.sub("", _NOMBRE.sub("", texto))
        if nombrado and not re.search(r"\w", resto):
            self.salida.pitido((660, 990))
            self._abrir_ventana()
            self.cerebro.calentar()
            return None

        # Cierre: "gracias, Luci", "Luci, silencio", "eso es todo"…
        if (_CIERRE.search(texto) and len(_palabras(resto)) <= 3 and "?" not in texto
                and (nombrado or en_ventana)):
            despedida = "Aquí estaré." if not re.search(r"gracias", texto, re.I) else "De nada. Aquí estaré."
            self._decir(despedida)
            self._cerrar_ventana(sonido=False)
            return None

        if nombrado:
            self.salida.pitido((1320,), duracion=0.035, volumen=0.05)   # "te oí"
        return self._responder(texto, t_fin)

    @staticmethod
    def _voz_pendiente(escuchado: list[tuple[np.ndarray, bool]]) -> list[np.ndarray] | None:
        """Audio desde poco antes de la primera voz, si hubo voz suficiente."""
        marcas = [v for _, v in escuchado]
        if sum(marcas) < 3:
            return None
        i = max(0, marcas.index(True) - 3)
        return [c for c, _ in escuchado[i:]]

    # ----------------------------------------------------------------- hablar
    def _decir(self, texto: str):
        self.estado = HABLANDO
        self._ev("luci", texto)
        audio, sr = self.voz.sintetizar(texto)
        self._ultimo_dicho = texto
        self.salida.encolar(audio, sr)
        while self.salida.sonando:
            time.sleep(0.05)
        time.sleep(0.2)
        self.mic.vaciar()

    def _responder(self, texto: str, t_fin_habla: float) -> list[np.ndarray] | None:
        """Responde en streaming. Si el modelo decide que no le hablaban a ella,
        calla. Si el usuario interrumpe, devuelve el audio con que empezó a hablar."""
        cfg_int = self.cfg["interrupcion"]
        cancelar, terminado = threading.Event(), threading.Event()
        tiempos: dict[str, float] = {}
        errores: list[Exception] = []
        resultado = {"silencio": False, "dicho": []}
        self.estado = PENSANDO

        def producir():
            fr = Fraseador()
            prefijo, decidido = "", False

            def enviar(frases):
                for f in frases:
                    if cancelar.is_set():
                        return
                    audio, sr = self.voz.sintetizar(f)
                    if cancelar.is_set():
                        return
                    tiempos.setdefault("audio", time.time() - t_fin_habla)
                    self.estado = HABLANDO
                    self.salida.encolar(audio, sr)
                    resultado["dicho"].append(f)
                    self._ev("luci", f)

            try:
                for delta in self.cerebro.responder(texto, cancelar):
                    tiempos.setdefault("token", time.time() - t_fin_habla)
                    if not decidido:
                        # ¿Empieza con [SILENCIO]? Se espera a tener suficiente texto.
                        prefijo += delta
                        p = prefijo.lstrip()
                        if not p or (p.startswith("[") and "]" not in p and len(p) < len(_SILENCIO) + 2):
                            continue
                        # [SILENCIO] o cualquier otra nota entre corchetes: no es para decir en voz alta
                        if p.startswith("["):
                            resultado["silencio"] = True
                            return
                        decidido, delta = True, p
                    enviar(fr.agregar(delta))
                enviar(fr.terminar())
            except Exception as e:  # red, clave, límites…
                errores.append(e)
            finally:
                terminado.set()

        threading.Thread(target=producir, daemon=True).start()

        # No se vacía el micrófono: lo dicho mientras se transcribía debe conservarse.
        necesarios = max(1, cfg_int.get("min_voz_ms", 300) // FRAME_MS)
        voz_usuario: list[np.ndarray] = []
        escuchado: list[tuple[np.ndarray, bool]] = []   # audio mientras Luci no suena
        while not (terminado.is_set() and not self.salida.sonando):
            cuadro = self.mic.leer(timeout=0.1)
            if cuadro is None or self.mic.silenciado:
                continue
            es_voz = self.vad.es_voz(cuadro)
            if not self.salida.sonando:
                escuchado.append((cuadro, es_voz))
            if not cfg_int.get("activa", True):
                continue
            # Hablando: se exige más volumen para no confundir su propio eco.
            # Pensando: basta el volumen mínimo de voz (evita cortar por ruidos de fondo).
            umbral = (cfg_int.get("umbral_rms", 1500) if self.salida.sonando
                      else self.cfg.get("filtro", {}).get("volumen_min", 80))
            if es_voz and rms(cuadro) >= umbral:
                voz_usuario.append(cuadro)
                if len(voz_usuario) >= necesarios:
                    cancelar.set()
                    self.salida.cortar()
                    self._ultimo_dicho = " ".join(resultado["dicho"])
                    self._ev("debug", "Interrumpido.")
                    self._abrir_ventana()
                    return voz_usuario
            else:
                voz_usuario.clear()

        self._ultimo_dicho = " ".join(resultado["dicho"])
        if errores:
            self._ev("error", f"Error del modelo: {type(errores[0]).__name__}: {str(errores[0])[:200]}")
            self._decir("Perdona, tengo problemas para conectarme con mi cerebro.")
        elif resultado["silencio"]:
            self._ev("debug", "El modelo decidió que no me hablaban a mí.")
        elif tiempos:
            self._ev("debug", f"Primera palabra {tiempos.get('token', 0):.2f}s · "
                              f"primer audio {tiempos.get('audio', 0):.2f}s desde que dejaste de hablar")
        if not resultado["dicho"]:
            # No habló (silencio o error): lo que se dijo mientras pensaba no se pierde.
            pendiente = self._voz_pendiente(escuchado)
            if resultado["silencio"]:
                self.estado = CONVERSANDO if time.time() < self.fin_ventana else ESPERA
            if pendiente:
                self._ev("debug", "Sigo escuchando lo que se dijo mientras pensaba.")
            return pendiente
        time.sleep(0.2)          # deja apagar la reverberación del parlante
        self.mic.vaciar()
        if resultado["silencio"]:
            # no se extiende la ventana si no le hablaban
            self.estado = CONVERSANDO if time.time() < self.fin_ventana else ESPERA
        else:
            self._abrir_ventana()
        return None
