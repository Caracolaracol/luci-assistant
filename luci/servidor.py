"""Servicio de Luci: ejecuta el motor y atiende a las interfaces por un socket local.

Protocolo: líneas JSON por un socket Unix (solo accesible para tu usuario).
  servicio → interfaz   {"t": "estado", ...}   ~10 veces por segundo
                        {"t": "evento", "tipo": ..., "texto": ..., "hora": ...}
                        {"t": "historial", "eventos": [...]}  al conectarse
                        {"t": "catalogo", "stt": [...], "voz": [...], "llm": [...]}
                        {"t": "aviso", "nivel": "info" | "error", "texto": ...}
  interfaz → servicio   {"cmd": "silenciar" | "conversar" | "nueva" | "modelo" | "apagar"}
                        {"cmd": "catalogo"}
                        {"cmd": "usar", "tipo": "stt" | "voz" | "llm", "id": ...}
                        {"cmd": "probar_voz", "id": ...}   (Luci dice una frase con esa voz)
                        {"cmd": "borrar", "tipo": "stt" | "voz", "id": ...}
                        {"cmd": "respaldo", "id": ..., "si": "1" | ""}   (lista de modelos de lenguaje)
                        {"cmd": "razonamiento", "valor": "none" | "low" | ...}
                        {"cmd": "clave", "valor": ...}   (clave de OpenCode Go)
                          (descarga el modelo si hace falta y lo guarda en config.toml)
"""

from __future__ import annotations

import collections
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

from . import modelos
from .config import SOCKET, cargar_config
from .estados import CARGANDO, CONVERSANDO, ERROR
from .motor import Evento, Motor


def _sketchybar() -> str | None:
    for ruta in (shutil.which("sketchybar"), "/opt/homebrew/bin/sketchybar", "/usr/local/bin/sketchybar"):
        if ruta and os.path.exists(ruta):
            return ruta
    return None


def avisar_barra(estado: str, cfg: dict):
    """Avisa a SketchyBar (si está instalado) del nuevo estado: evento `luci_estado`."""
    if not cfg.get("integraciones", {}).get("sketchybar", True):
        return
    binario = _sketchybar()
    if binario:
        subprocess.Popen([binario, "--trigger", "luci_estado", f"ESTADO={estado}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Servidor:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._clientes: set[socket.socket] = set()
        self._lock = threading.Lock()
        self._historial: collections.deque[dict] = collections.deque(maxlen=200)
        self._activo = True
        self._fallo: str | None = None
        self.motor = Motor(cfg, self._al_evento)
        # Los servidores MCP inician después de cargar: la ventana necesita la lista nueva.
        self.motor.al_cambiar_herramientas = lambda: self._difundir(self._catalogo())
        self._modelos = cfg["llm"].get("candidatos") or [cfg["llm"]["modelo"]]
        self._descarga: dict | None = None      # {"tipo", "id", "nombre", "progreso"}
        self._llm_disponibles: list[str] = []   # modelos que ofrece OpenCode Go

    # ------------------------------------------------------------- difusión
    def _enviar(self, c: socket.socket, msg: dict) -> bool:
        try:
            c.sendall((json.dumps(msg, ensure_ascii=False) + "\n").encode())
            return True
        except OSError:
            return False

    def _difundir(self, msg: dict):
        with self._lock:
            caidos = [c for c in self._clientes if not self._enviar(c, msg)]
            for c in caidos:
                self._clientes.discard(c)
                c.close()

    def _al_evento(self, ev: Evento):
        msg = {"t": "evento", "tipo": ev.tipo, "texto": ev.texto, "hora": time.strftime("%H:%M:%S")}
        # El registro en disco no guarda lo hablado salvo que se active en config.toml.
        texto = ev.texto
        if ev.tipo in ("usuario", "luci", "oido") and not self.cfg.get("registro", {}).get("conversaciones", False):
            texto = f"({len(ev.texto.split())} palabras)"
        print(f"[{msg['hora']}] {ev.tipo}: {texto}", flush=True)
        with self._lock:
            self._historial.append(msg)
        self._difundir(msg)

    def _estado(self) -> dict:
        m = self.motor
        return {
            "t": "estado",
            "estado": ERROR if self._fallo else m.estado,
            "nivel": round(m.nivel, 3),
            "modelo": m.modelo,
            "restante": max(0.0, m.fin_ventana - time.time()) if m.estado == CONVERSANDO else 0.0,
            "stt": self.cfg["stt"]["modelo"].split("/")[-1],
            "pid": os.getpid(),
            "descarga": self._descarga,
        }

    def _catalogo(self) -> dict:
        cat = modelos.catalogo(self.cfg, self._descarga, self._llm_disponibles)
        h = getattr(self.motor, "herramientas", None)
        cat["herramientas"] = h.resumen() if h else []
        from .mcp import id_valido, importables

        ya = set(h.servidores) if h else set()
        cat["importables"] = [{"clave": k, "nombre": v["nombre"], "origen": v["origen"],
                               "tipo": "remoto" if v.get("url") else "local",
                               "detalle": v.get("url", "").split("?")[0] or
                                          " ".join([v.get("comando", ""), *v.get("args", [])])}
                              for k, v in importables().items() if id_valido(v["nombre"]) not in ya]
        return cat

    # ------------------------------------------------------- herramientas (MCP)
    def _mcp(self, cmd: str, msg: dict):
        """Agregar, importar, quitar, activar y permitir herramientas de servidores MCP.
        Todo se guarda en config.toml, en [herramientas.<id>]."""
        from . import mcp

        h = getattr(self.motor, "herramientas", None)
        if h is None:
            return self._aviso("Espera a que Luci termine de cargar.", "error")
        avisar = lambda texto: self._al_evento(Evento("info", texto))   # noqa: E731
        id_ = str(msg.get("id", ""))
        seccion = f"herramientas.{id_}"
        try:
            if cmd in ("mcp_agregar", "mcp_importar"):
                if cmd == "mcp_agregar":
                    id_ = mcp.id_valido(str(msg.get("nombre", "")))
                    cfg = mcp.desde_formulario(str(msg.get("tipo", "local")), str(msg.get("comando", "")),
                                               str(msg.get("url", "")), str(msg.get("variables", "")),
                                               str(msg.get("descripcion", "")))
                else:
                    origen = mcp.importables().get(str(msg.get("clave", "")))
                    if not origen:
                        return self._aviso("No encontré ese servidor.", "error")
                    id_ = mcp.id_valido(origen["nombre"])
                    cfg = {k: v for k, v in origen.items() if k not in ("nombre", "origen")}
                    cfg["descripcion"] = str(msg.get("descripcion", "")).strip()
                    cfg["activo"] = True
                if not id_:
                    return self._aviso("Falta el nombre del servidor.", "error")
                s = h.agregar(id_, cfg, avisar)
                modelos.guardar_seccion(f"herramientas.{id_}", s.cfg)
                self.cfg.setdefault("herramientas", {})[id_] = cfg
                if s.error:
                    self._aviso(f"Guardé «{id_}», pero no pude conectarme: {s.error}", "error")
                else:
                    self._aviso(f"«{id_}» agregado con {len(s.todas)} herramientas; "
                                f"quedaron permitidas {len(s.herramientas)}, las de solo lectura.")
            elif cmd == "mcp_quitar":
                h.quitar(id_)
                modelos.quitar_seccion(seccion)
                if id_ in self._predeterminadas():
                    # Viene en la configuración por defecto: se marca para que no vuelva.
                    modelos.guardar_seccion(seccion, {"quitado": True})
                self.cfg.get("herramientas", {}).pop(id_, None)
                avisar(f"Servidor «{id_}» quitado.")
            elif cmd == "mcp_activar":
                si = bool(msg.get("si"))
                h.activar(id_, si, avisar)
                modelos.guardar_opcion(seccion, "activo", si)
            elif cmd == "mcp_permitir":
                lista = h.permitir(id_, str(msg.get("herramienta", "")), bool(msg.get("si")))
                modelos.guardar_opcion(seccion, "permitidas", lista)
            elif cmd == "mcp_descripcion":
                texto = str(msg.get("descripcion", "")).strip()
                h.servidores[id_].descripcion = texto
                modelos.guardar_opcion(seccion, "descripcion", texto)
        except Exception as e:      # noqa: BLE001
            self._aviso(f"No pude cambiar las herramientas: {e}", "error")
        self._difundir(self._catalogo())

    @staticmethod
    def _predeterminadas() -> set[str]:
        import tomllib

        from .config import PREDETERMINADO

        with open(PREDETERMINADO / "config.toml", "rb") as f:
            return set(tomllib.load(f).get("herramientas", {}))

    def _consultar_modelos(self):
        from .config import buscar_clave
        from .llm import listar_modelos

        try:
            self._llm_disponibles = listar_modelos(self.cfg["llm"]["base_url"], buscar_clave() or "")
        except Exception as e:      # noqa: BLE001 — sin red o sin clave: solo la lista propia
            print(f"No pude obtener la lista de modelos: {e}", flush=True)
            return
        self._difundir(self._catalogo())

    # ------------------------------------------------- modelo de lenguaje
    def _candidatos(self, lista: list[str]):
        """Guarda la lista de modelos (en uso + respaldo, en orden) y la aplica."""
        lista = list(dict.fromkeys(lista))
        self.cfg["llm"]["candidatos"] = lista
        self._modelos = lista
        modelos.guardar_opcion("llm", "candidatos", lista)
        if hasattr(self.motor, "cerebro"):
            self.motor.cerebro.respaldo = lista

    def _ajuste_llm(self, cmd: str, msg: dict):
        l = self.cfg["llm"]
        try:
            if cmd == "respaldo":
                id_ = str(msg.get("id", ""))
                lista = list(l.get("candidatos") or [l["modelo"]])
                if msg.get("si"):
                    lista.append(id_)
                elif id_ != l["modelo"]:            # el modelo en uso no se quita
                    lista = [m for m in lista if m != id_]
                self._candidatos(lista)
            elif cmd == "razonamiento":
                valor = str(msg.get("valor", "none"))
                if valor in ("none", "minimal", "low", "medium", "high"):
                    l["razonamiento"] = valor
                    modelos.guardar_opcion("llm", "razonamiento", valor)
                    if hasattr(self.motor, "cerebro"):
                        self.motor.cerebro.razonamiento = valor
            elif cmd == "clave":
                from .config import guardar_clave

                clave = str(msg.get("valor", "")).strip()
                if len(clave) < 10:
                    return self._aviso("Esa clave parece incompleta.", "error")
                guardar_clave(clave)
                if hasattr(self.motor, "cerebro"):
                    self.motor.cerebro.cambiar_clave(clave)
                self._aviso("Clave de OpenCode Go guardada.")
                threading.Thread(target=self._consultar_modelos, daemon=True).start()
        except Exception as e:      # noqa: BLE001
            self._aviso(f"No pude guardar el ajuste: {e}", "error")
        self._difundir(self._catalogo())

    def _borrar(self, tipo: str, id_: str):
        actual = (self.cfg["stt"]["modelo"] if tipo == "stt"
                  else Path(self.cfg["tts"].get("voz_piper", "")).stem
                  if self.cfg["tts"].get("motor") != "say" else "")
        if id_ == actual:
            return self._aviso("No se puede borrar el modelo en uso: elige otro primero.", "error")
        if self._descarga and self._descarga["id"] == id_:
            return
        try:
            modelos.borrar(tipo, id_)
            self._al_evento(Evento("info", f"Modelo borrado: {id_}"))
        except Exception as e:      # noqa: BLE001
            self._aviso(f"No pude borrar {id_}: {e}", "error")
        self._difundir(self._catalogo())

    def _aviso(self, texto: str, nivel: str = "info"):
        self._difundir({"t": "aviso", "nivel": nivel, "texto": texto})
        self._al_evento(Evento("error" if nivel == "error" else "info", texto))

    # ------------------------------------------------------- modelos y voces
    def _usar(self, tipo: str, id_: str):
        if tipo == "llm":
            if id_ in self._modelos or id_ in self._llm_disponibles:
                modelos.guardar_opcion("llm", "modelo", id_)
                self.cfg["llm"]["modelo"] = id_
                self.motor.cambiar_modelo(id_)
                # El modelo elegido encabeza la lista; los demás quedan de respaldo.
                self._candidatos([id_] + [m for m in self._modelos if m != id_])
            return self._difundir(self._catalogo())
        if tipo == "voz" and id_.startswith("say:"):
            return self._aplicar("voz", id_)
        m = modelos.buscar(tipo, id_)
        if m is None:
            return
        instalado = (modelos.whisper_instalado(id_) if tipo == "stt" else modelos.piper_instalada(id_))
        if instalado:
            return self._aplicar(tipo, id_)
        if self._descarga:
            return self._aviso("Ya hay una descarga en curso; espera a que termine.", "error")
        self._descarga = {"tipo": tipo, "id": id_, "nombre": m.nombre, "progreso": 0.0}
        self._difundir(self._catalogo())
        threading.Thread(target=self._descargar, args=(tipo, m), daemon=True).start()

    def _descargar(self, tipo: str, m: "modelos.Modelo"):
        def progreso(p: float):
            if self._descarga:
                self._descarga["progreso"] = round(p, 3)

        self._al_evento(Evento("info", f"Descargando {m.nombre}…"))
        try:
            (modelos.descargar_whisper if tipo == "stt" else modelos.descargar_piper)(m, progreso)
        except Exception as e:      # noqa: BLE001
            self._descarga = None
            self._aviso(f"No pude descargar {m.nombre}: {e}", "error")
            return self._difundir(self._catalogo())
        self._descarga = None
        self._aviso(f"{m.nombre} descargado.")
        self._aplicar(tipo, m.id)

    def _probar_voz(self, id_: str):
        """Dice una frase con la voz indicada, sin cambiar la voz en uso."""
        from .config import ruta_datos
        from .tts import crear_voz

        if id_.startswith("say:"):
            tts = {"motor": "say", "voz_say": id_[4:]}
        elif modelos.piper_instalada(id_):
            tts = {"motor": "piper", "voz_piper": f"models/piper/{id_}.onnx",
                   "lentitud_piper": self.cfg["tts"].get("lentitud_piper", 1.0)}
        else:
            return
        texto = "Hola, soy Luci. Así sueno con esta voz."
        try:
            audio, sr = crear_voz(tts, ruta_datos).sintetizar(texto)
        except Exception as e:      # noqa: BLE001
            return self._aviso(f"No pude probar la voz: {e}", "error")
        self.motor._ultimo_dicho = texto      # que no se escuche a sí misma como si le hablaran
        self.motor.salida.encolar(audio, sr)

    def _aplicar(self, tipo: str, id_: str):
        """Guarda la elección en config.toml y la aplica sin reiniciar."""
        try:
            if tipo == "stt":
                modelos.guardar_opcion("stt", "modelo", id_)
                self.cfg["stt"]["modelo"] = id_
                self.motor.cambiar_stt(id_)
            elif tipo == "voz":
                tts = self.cfg["tts"]
                if id_.startswith("say:"):
                    tts["motor"], tts["voz_say"] = "say", id_[4:]
                    modelos.guardar_opcion("tts", "voz_say", tts["voz_say"])
                else:
                    tts["motor"], tts["voz_piper"] = "piper", f"models/piper/{id_}.onnx"
                    modelos.guardar_opcion("tts", "voz_piper", tts["voz_piper"])
                modelos.guardar_opcion("tts", "motor", tts["motor"])
                self.motor.cambiar_voz(tts)
        except Exception as e:      # noqa: BLE001
            self._aviso(f"No pude cambiar el modelo: {e}", "error")
        self._difundir(self._catalogo())

    # ------------------------------------------------------------- comandos
    def _comando(self, msg: dict, c: socket.socket):
        cmd = msg.get("cmd", "")
        m = self.motor
        if cmd == "catalogo":
            self._enviar(c, self._catalogo())
            return
        if cmd == "apagar":
            self._al_evento(Evento("info", "Apagando el servicio…"))
            self.detener()
            return
        # Ajustes que no necesitan el motor listo (p. ej. corregir una clave que falló).
        if cmd == "borrar":
            threading.Thread(target=self._borrar, args=(str(msg.get("tipo", "")), str(msg.get("id", ""))),
                             daemon=True).start()
            return
        if cmd.startswith("mcp_"):
            threading.Thread(target=self._mcp, args=(cmd, msg), daemon=True).start()
            return
        if cmd in ("respaldo", "razonamiento", "clave"):
            return self._ajuste_llm(cmd, msg)
        if self._fallo or m.estado == CARGANDO:
            return
        if cmd == "silenciar":
            m.silenciar(not m.mic.silenciado)
        elif cmd == "conversar":
            m.abrir_conversacion()
        elif cmd == "nueva":
            m.nueva_conversacion()
        elif cmd == "modelo" and len(self._modelos) > 1:
            i = self._modelos.index(m.modelo) if m.modelo in self._modelos else -1
            m.cambiar_modelo(self._modelos[(i + 1) % len(self._modelos)])
        elif cmd == "probar_voz":
            threading.Thread(target=self._probar_voz, args=(str(msg.get("id", "")),), daemon=True).start()
        elif cmd == "usar":
            threading.Thread(target=self._usar, args=(msg.get("tipo", ""), str(msg.get("id", ""))),
                             daemon=True).start()

    def _atender(self, c: socket.socket):
        with self._lock:
            historial = list(self._historial)
        self._enviar(c, {"t": "historial", "eventos": historial})
        self._enviar(c, self._estado())
        with self._lock:
            self._clientes.add(c)
        buffer = b""
        try:
            while self._activo:
                datos = c.recv(4096)
                if not datos:
                    break
                buffer += datos
                while b"\n" in buffer:
                    linea, buffer = buffer.split(b"\n", 1)
                    try:
                        self._comando(json.loads(linea), c)
                    except (json.JSONDecodeError, AttributeError):
                        pass
        except OSError:
            pass
        finally:
            with self._lock:
                self._clientes.discard(c)
            c.close()

    # ---------------------------------------------------------------- ciclo
    def _correr_motor(self):
        try:
            self.motor.ejecutar()
        except Exception as e:
            self._fallo = f"{type(e).__name__}: {e}"
            self._al_evento(Evento("error", f"El motor se detuvo: {self._fallo}"))

    def _latido(self):
        anterior = None
        while self._activo:
            estado = self._estado()
            self._difundir(estado)
            if estado["estado"] != anterior:
                anterior = estado["estado"]
                avisar_barra(anterior, self.cfg)
            time.sleep(0.1)

    def detener(self):
        self._activo = False
        self.motor.detener()
        try:
            self._socket.close()
        except OSError:
            pass

    def ejecutar(self):
        SOCKET.parent.mkdir(parents=True, exist_ok=True)
        if SOCKET.exists():
            # ¿Ya hay un servicio corriendo?
            prueba = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                prueba.connect(str(SOCKET))
                raise SystemExit("Luci ya está corriendo.")
            except (ConnectionRefusedError, FileNotFoundError):
                SOCKET.unlink(missing_ok=True)
            finally:
                prueba.close()
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.bind(str(SOCKET))
        os.chmod(SOCKET, 0o600)
        self._socket.listen()
        print(f"Luci escuchando en {SOCKET}", flush=True)

        threading.Thread(target=self._correr_motor, daemon=True).start()
        threading.Thread(target=self._latido, daemon=True).start()
        threading.Thread(target=self._consultar_modelos, daemon=True).start()
        try:
            while self._activo:
                try:
                    c, _ = self._socket.accept()
                except OSError:
                    break
                threading.Thread(target=self._atender, args=(c,), daemon=True).start()
        finally:
            self._activo = False
            self.motor.detener()
            SOCKET.unlink(missing_ok=True)
            avisar_barra("apagada", self.cfg)
            time.sleep(0.5)        # deja que el motor libere el audio


def ejecutar_servicio():
    import signal

    srv = Servidor(cargar_config())
    signal.signal(signal.SIGTERM, lambda *_: srv.detener())
    try:
        srv.ejecutar()
    except KeyboardInterrupt:
        srv.detener()
