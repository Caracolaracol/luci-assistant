"""Servicio de Luci: ejecuta el motor y atiende a las interfaces por un socket local.

Protocolo: líneas JSON por un socket Unix (solo accesible para tu usuario).
  servicio → interfaz   {"t": "estado", ...}   ~10 veces por segundo
                        {"t": "evento", "tipo": ..., "texto": ..., "hora": ...}
                        {"t": "historial", "eventos": [...]}  al conectarse
  interfaz → servicio   {"cmd": "silenciar" | "conversar" | "nueva" | "modelo" | "apagar"}
"""

from __future__ import annotations

import collections
import json
import os
import socket
import threading
import time

from .config import SOCKET, cargar_config
from .estados import CARGANDO, CONVERSANDO, ERROR
from .motor import Evento, Motor


class Servidor:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._clientes: set[socket.socket] = set()
        self._lock = threading.Lock()
        self._historial: collections.deque[dict] = collections.deque(maxlen=200)
        self._activo = True
        self._fallo: str | None = None
        self.motor = Motor(cfg, self._al_evento)
        self._modelos = cfg["llm"].get("candidatos") or [cfg["llm"]["modelo"]]

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
        }

    # ------------------------------------------------------------- comandos
    def _comando(self, cmd: str):
        m = self.motor
        if cmd == "apagar":
            self._al_evento(Evento("info", "Apagando el servicio…"))
            self.detener()
            return
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
                        self._comando(json.loads(linea).get("cmd", ""))
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
        while self._activo:
            self._difundir(self._estado())
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
            time.sleep(0.5)        # deja que el motor libere el audio


def ejecutar_servicio():
    import signal

    srv = Servidor(cargar_config())
    signal.signal(signal.SIGTERM, lambda *_: srv.detener())
    try:
        srv.ejecutar()
    except KeyboardInterrupt:
        srv.detener()
