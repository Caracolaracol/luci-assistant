"""Conexión de una interfaz con el servicio de Luci."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Callable

from .config import SOCKET


class Cliente:
    """Mantiene la conexión con el servicio (y reconecta si se corta).

    `al_mensaje` se llama desde un hilo propio con cada mensaje recibido.
    """

    def __init__(self, al_mensaje: Callable[[dict], None]):
        self._al_mensaje = al_mensaje
        self._socket: socket.socket | None = None
        self._lock = threading.Lock()
        self._activo = True
        self.conectado = False

    def iniciar(self):
        threading.Thread(target=self._bucle, daemon=True).start()

    def cerrar(self):
        self._activo = False
        with self._lock:
            if self._socket:
                self._socket.close()

    def enviar(self, cmd: str, **extra) -> bool:
        with self._lock:
            if not self._socket:
                return False
            try:
                self._socket.sendall((json.dumps({"cmd": cmd, **extra}) + "\n").encode())
                return True
            except OSError:
                return False

    def _bucle(self):
        while self._activo:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                s.connect(str(SOCKET))
            except OSError:
                s.close()
                self._cambio_conexion(False)
                time.sleep(1)
                continue
            with self._lock:
                self._socket = s
            self._cambio_conexion(True)
            buffer = b""
            try:
                while self._activo:
                    datos = s.recv(65536)
                    if not datos:
                        break
                    buffer += datos
                    while b"\n" in buffer:
                        linea, buffer = buffer.split(b"\n", 1)
                        try:
                            self._al_mensaje(json.loads(linea))
                        except json.JSONDecodeError:
                            pass
            except OSError:
                pass
            with self._lock:
                self._socket = None
            s.close()
            self._cambio_conexion(False)
            time.sleep(0.5)

    def _cambio_conexion(self, conectado: bool):
        if conectado != self.conectado:
            self.conectado = conectado
            self._al_mensaje({"t": "conexion", "conectado": conectado})


def servicio_activo() -> bool:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(str(SOCKET))
        return True
    except OSError:
        return False
    finally:
        s.close()


def consultar(cmd: str | None = None, timeout: float = 2.0) -> dict | None:
    """Conexión breve: devuelve el estado actual del servicio y, opcionalmente, envía un
    comando. None si el servicio no responde. Pensado para scripts (SketchyBar, etc.)."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(SOCKET))
        estado, buffer = None, b""
        while estado is None:
            datos = s.recv(65536)
            if not datos:
                return None
            buffer += datos
            while b"\n" in buffer and estado is None:
                linea, buffer = buffer.split(b"\n", 1)
                try:
                    msg = json.loads(linea)
                except json.JSONDecodeError:
                    continue
                if msg.get("t") == "estado":
                    estado = msg
        if cmd:
            s.sendall((json.dumps({"cmd": cmd}) + "\n").encode())
        return estado
    except OSError:
        return None
    finally:
        s.close()
