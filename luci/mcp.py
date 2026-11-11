"""Herramientas para el modelo de lenguaje vía MCP (Model Context Protocol).

Luci actúa como cliente MCP, igual que Claude Code, OpenCode o Pi: se conecta a cada
servidor configurado en [herramientas.<nombre>] de config.toml, le pide su lista de
herramientas y le ofrece al modelo las permitidas. Cuando el modelo pide usar una, Luci
la ejecuta y le devuelve el resultado.

Dos tipos de servidor:
- local (stdio): `comando` + `args` (+ `env`); Luci lo inicia como un proceso.
- remoto (HTTP «streamable»): `url` (+ `headers`).

Ningún servidor necesita código propio: se agregan desde la ventana de ajustes (a mano o
importados de Pi, Claude Code u OpenCode) o editando config.toml.

`permitidas` limita qué herramientas ve el modelo. Al agregar un servidor desde la ventana
se permiten solo las que el servidor declara de solo lectura (`readOnlyHint`): por voz no
hay confirmación antes de actuar.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import Future
from pathlib import Path

VERSION_PROTOCOLO = "2025-06-18"
MAX_RESULTADO = 6000          # caracteres de cada resultado que se envían al modelo
_RUTAS_EXTRA = ("/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local/bin"))
_CLIENTE = {"name": "luci", "version": "0.2"}


def _resolver(comando: str) -> str | None:
    """El servicio arranca con un PATH mínimo: también busca en las rutas de Homebrew."""
    if os.path.isabs(comando):
        return comando if os.access(comando, os.X_OK) else None
    ruta = os.pathsep.join([os.environ.get("PATH", ""), *_RUTAS_EXTRA])
    return shutil.which(comando, path=ruta)


def _ruta_procesos() -> str:
    return os.pathsep.join([*_RUTAS_EXTRA, os.environ.get("PATH", "/usr/bin:/bin")])


def id_valido(nombre: str) -> str:
    """Nombre del servidor apto para config.toml y para el nombre de la herramienta."""
    return re.sub(r"[^a-z0-9_-]+", "-", nombre.strip().lower()).strip("-")[:30]


# ----------------------------------------------------------------- servidores
class ServidorMCP:
    """Base común: estado, herramientas y permisos. Las subclases implementan el transporte."""

    tipo = ""

    def __init__(self, nombre: str, cfg: dict):
        self.nombre = nombre
        self.cfg = cfg
        self.descripcion = cfg.get("descripcion", "")
        p = cfg.get("permitidas")
        self.permitidas: set[str] | None = set(p) if p is not None else None   # None = todas
        self.todas: list[dict] = []          # herramientas tal como las describe el servidor
        self.error = ""
        self._lock = threading.Lock()
        self._siguiente = 0

    @property
    def herramientas(self) -> list[dict]:
        return [h for h in self.todas if self.permitidas is None or h["name"] in self.permitidas]

    @property
    def disponible(self) -> bool:
        return True

    @property
    def detalle(self) -> str:
        return ""

    def _id(self) -> int:
        with self._lock:
            self._siguiente += 1
            return self._siguiente

    def iniciar(self, espera: float = 15.0):
        self._conectar()
        self._pedir("initialize", {"protocolVersion": VERSION_PROTOCOLO, "capabilities": {},
                                   "clientInfo": _CLIENTE}, espera)
        self._notificar("notifications/initialized")
        self.todas = self._pedir("tools/list", {}, espera).get("tools", [])
        self.error = ""

    def llamar(self, herramienta: str, argumentos: dict, espera: float = 15.0) -> str:
        if self.permitidas is not None and herramienta not in self.permitidas:
            return f"La herramienta {herramienta} no está permitida."
        if not self.conectado():
            self.iniciar()         # se reconecta si el servidor se cayó
        r = self._pedir("tools/call", {"name": herramienta, "arguments": argumentos}, espera)
        texto = "\n".join(c.get("text", "") for c in r.get("content", []) if c.get("type") == "text")
        if r.get("isError"):
            texto = f"Error: {texto}"
        return texto[:MAX_RESULTADO] + ("\n…(recortado)" if len(texto) > MAX_RESULTADO else "")

    # transporte
    def _conectar(self): ...
    def conectado(self) -> bool: ...
    def cerrar(self): ...
    def _pedir(self, metodo: str, params: dict, espera: float) -> dict: ...
    def _notificar(self, metodo: str): ...


class ServidorLocal(ServidorMCP):
    """Servidor iniciado como proceso: mensajes JSON-RPC por stdin/stdout, uno por línea."""

    tipo = "local"

    def __init__(self, nombre: str, cfg: dict):
        super().__init__(nombre, cfg)
        self._proc: subprocess.Popen | None = None
        self._pendientes: dict[int, Future] = {}

    @property
    def disponible(self) -> bool:
        return _resolver(self.cfg["comando"]) is not None

    @property
    def detalle(self) -> str:
        return " ".join([self.cfg["comando"], *self.cfg.get("args", [])])

    def _conectar(self):
        ejecutable = _resolver(self.cfg["comando"])
        if not ejecutable:
            raise FileNotFoundError(f"no encontré «{self.cfg['comando']}»")
        self.cerrar()
        self._proc = subprocess.Popen(
            [ejecutable, *self.cfg.get("args", [])],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={**os.environ, "PATH": _ruta_procesos(), **(self.cfg.get("env") or {})},
            cwd=Path.home(), text=True, encoding="utf-8", bufsize=1,
        )
        threading.Thread(target=self._leer, args=(self._proc,), daemon=True).start()

    def conectado(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def cerrar(self):
        if self.conectado():
            self._proc.terminate()
        self._proc = None

    def _leer(self, proc: subprocess.Popen):
        for linea in proc.stdout:
            try:
                msg = json.loads(linea)
            except json.JSONDecodeError:
                continue
            futuro = self._pendientes.pop(msg.get("id"), None) if "id" in msg else None
            if futuro is None:
                continue          # notificaciones o pedidos del servidor: no se usan
            if "error" in msg:
                futuro.set_exception(RuntimeError(msg["error"].get("message", "error MCP")))
            else:
                futuro.set_result(msg.get("result", {}))
        for id_, futuro in list(self._pendientes.items()):
            self._pendientes.pop(id_, None)
            futuro.set_exception(ConnectionError(f"{self.nombre} se cerró"))

    def _escribir(self, msg: dict):
        with self._lock:
            self._proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()

    def _pedir(self, metodo: str, params: dict, espera: float) -> dict:
        id_ = self._id()
        futuro: Future = Future()
        self._pendientes[id_] = futuro
        self._escribir({"jsonrpc": "2.0", "id": id_, "method": metodo, "params": params})
        try:
            return futuro.result(timeout=espera)
        finally:
            self._pendientes.pop(id_, None)

    def _notificar(self, metodo: str):
        self._escribir({"jsonrpc": "2.0", "method": metodo})


class ServidorRemoto(ServidorMCP):
    """Servidor por HTTP («streamable HTTP»): cada mensaje es un POST; la respuesta llega
    como JSON o como un flujo de eventos (SSE)."""

    tipo = "remoto"

    def __init__(self, nombre: str, cfg: dict):
        super().__init__(nombre, cfg)
        self._sesion: str | None = None
        self._iniciado = False
        host = urllib.parse.urlparse(cfg["url"]).hostname or ""
        self._ssl = ssl.create_default_context()
        if host in ("127.0.0.1", "localhost", "::1"):
            # Servidores en este Mac (p. ej. el plugin de Obsidian) usan certificados propios.
            self._ssl.check_hostname = False
            self._ssl.verify_mode = ssl.CERT_NONE

    @property
    def detalle(self) -> str:
        u = urllib.parse.urlparse(self.cfg["url"])
        return f"{u.scheme}://{u.netloc}{u.path}"

    def _conectar(self):
        self._sesion = None
        self._iniciado = True

    def conectado(self) -> bool:
        return self._iniciado

    def cerrar(self):
        self._iniciado = False
        self._sesion = None

    def _post(self, msg: dict, espera: float):
        cabeceras = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                     "MCP-Protocol-Version": VERSION_PROTOCOLO, **(self.cfg.get("headers") or {})}
        if self._sesion:
            cabeceras["Mcp-Session-Id"] = self._sesion
        pedido = urllib.request.Request(self.cfg["url"], json.dumps(msg).encode(), cabeceras, method="POST")
        r = urllib.request.urlopen(pedido, timeout=espera, context=self._ssl)
        self._sesion = r.headers.get("Mcp-Session-Id") or self._sesion
        return r

    def _pedir(self, metodo: str, params: dict, espera: float) -> dict:
        id_ = self._id()
        try:
            r = self._post({"jsonrpc": "2.0", "id": id_, "method": metodo, "params": params}, espera)
        except urllib.error.HTTPError as e:
            if e.code == 404 and self._sesion:      # sesión vencida: hay que volver a iniciar
                self._iniciado = False
            raise RuntimeError(f"HTTP {e.code}") from None
        with r:
            if "text/event-stream" in (r.headers.get("Content-Type") or ""):
                datos: list[str] = []
                for linea in r:
                    linea = linea.decode("utf-8").rstrip("\r\n")
                    if linea.startswith("data:"):
                        datos.append(linea[5:].lstrip())
                    elif not linea and datos:
                        msg = self._mensaje("\n".join(datos), id_)
                        datos = []
                        if msg is not None:
                            return msg
                raise ConnectionError("el servidor cerró el flujo sin responder")
            return self._mensaje(r.read().decode("utf-8"), id_) or {}

    @staticmethod
    def _mensaje(texto: str, id_: int) -> dict | None:
        try:
            msg = json.loads(texto)
        except json.JSONDecodeError:
            return None
        for m in msg if isinstance(msg, list) else [msg]:
            if m.get("id") == id_:
                if "error" in m:
                    raise RuntimeError(m["error"].get("message", "error MCP"))
                return m.get("result", {})
        return None

    def _notificar(self, metodo: str):
        try:
            self._post({"jsonrpc": "2.0", "method": metodo}, 10).close()
        except urllib.error.HTTPError:
            pass


def crear_servidor(nombre: str, cfg: dict) -> ServidorMCP:
    return ServidorRemoto(nombre, cfg) if cfg.get("url") else ServidorLocal(nombre, cfg)


# --------------------------------------------------------------- conjunto
class Herramientas:
    """Los servidores MCP configurados, con sus herramientas en el formato de OpenAI."""

    SEPARADOR = "__"     # nombre para el modelo: engram__mem_search

    def __init__(self, cfg: dict):
        self.servidores: dict[str, ServidorMCP] = {}
        self.activos: set[str] = set()
        # Guarda la sección de un servidor en config.toml (lo define quien crea el conjunto).
        self.al_guardar = lambda nombre, cfg: None
        for nombre, s in (cfg or {}).items():
            if not isinstance(s, dict) or s.get("quitado") or not (s.get("comando") or s.get("url")):
                continue
            self.servidores[nombre] = crear_servidor(nombre, s)
            if s.get("activo", True):
                self.activos.add(nombre)

    def iniciar(self, avisar=lambda texto: None, reintento_s: float = 60):
        """Inicia los servidores activos (en segundo plano: no retrasa la carga de Luci).
        Los que no respondan (p. ej. Obsidian cerrado) se reintentan cada `reintento_s`."""
        for nombre in list(self.activos):
            threading.Thread(target=self.iniciar_uno, args=(nombre, avisar), daemon=True).start()

        def reintentar():
            while True:
                time.sleep(reintento_s)
                for nombre in list(self.activos):
                    s = self.servidores.get(nombre)
                    if s and s.error and s.disponible:
                        self.iniciar_uno(nombre, avisar, callado=True)

        threading.Thread(target=reintentar, daemon=True).start()

    def iniciar_uno(self, nombre: str, avisar=lambda texto: None, callado: bool = False) -> bool:
        s = self.servidores[nombre]
        try:
            s.iniciar()
            if s.cfg.pop("revisar_permisos", False):
                # Servidor recién agregado: se permiten solo las herramientas de lectura.
                s.permitidas = {h["name"] for h in s.todas
                                if (h.get("annotations") or {}).get("readOnlyHint")}
                s.cfg["permitidas"] = sorted(s.permitidas)
                self.al_guardar(nombre, s.cfg)
            avisar(f"Herramientas de {nombre}: {', '.join(h['name'] for h in s.herramientas) or 'ninguna permitida'}")
            return True
        except Exception as e:      # noqa: BLE001
            s.cerrar()
            if not (callado and s.error):
                avisar(f"No pude iniciar {nombre}: {str(e) or type(e).__name__}")
            s.error = str(e) or type(e).__name__
            return False

    def agregar(self, nombre: str, cfg: dict, avisar=lambda texto: None) -> ServidorMCP:
        """Agrega (o reemplaza) un servidor y lo inicia. Si no trae `permitidas`, empieza sin
        ninguna y, en cuanto logra conectarse, permite solo las de solo lectura."""
        if nombre in self.servidores:
            self.servidores[nombre].cerrar()
        if "permitidas" not in cfg:
            cfg["permitidas"], cfg["revisar_permisos"] = [], True
        s = crear_servidor(nombre, cfg)
        self.servidores[nombre] = s
        self.activos.add(nombre)
        self.iniciar_uno(nombre, avisar)
        return s

    def quitar(self, nombre: str):
        s = self.servidores.pop(nombre, None)
        self.activos.discard(nombre)
        if s:
            s.cerrar()

    def activar(self, nombre: str, si: bool, avisar=lambda texto: None):
        if nombre not in self.servidores:
            return
        if si:
            self.activos.add(nombre)
            self.iniciar_uno(nombre, avisar)
        else:
            self.activos.discard(nombre)
            self.servidores[nombre].cerrar()

    def permitir(self, nombre: str, herramienta: str, si: bool) -> list[str]:
        s = self.servidores[nombre]
        actuales = set(s.permitidas) if s.permitidas is not None else {h["name"] for h in s.todas}
        (actuales.add if si else actuales.discard)(herramienta)
        s.permitidas = actuales
        return sorted(actuales)

    def esquemas(self) -> list[dict]:
        return [
            {"type": "function", "function": {
                "name": f"{n}{self.SEPARADOR}{h['name']}"[:64],
                "description": h.get("description", "")[:1000],
                "parameters": h.get("inputSchema") or {"type": "object", "properties": {}},
            }}
            for n in sorted(self.activos) for h in self.servidores[n].herramientas
        ]

    def instrucciones(self) -> str:
        """Texto para el prompt de sistema: qué servidor sirve para qué."""
        return "\n".join(
            f"- {n}: {self.servidores[n].descripcion or 'sin descripción'}"
            for n in sorted(self.activos) if self.servidores[n].herramientas)

    def llamar(self, nombre: str, argumentos: str) -> str:
        servidor, _, herramienta = nombre.partition(self.SEPARADOR)
        if servidor not in self.activos:
            return f"La herramienta {nombre} no está disponible."
        try:
            args = json.loads(argumentos or "{}")
        except json.JSONDecodeError:
            return "Error: argumentos inválidos (no son JSON)."
        try:
            return self.servidores[servidor].llamar(herramienta, args)
        except Exception as e:      # noqa: BLE001
            return f"Error al usar {nombre}: {e}"

    def resumen(self) -> list[dict]:
        """Para la ventana de ajustes (sin claves: solo nombres de variables y cabeceras)."""
        lista = []
        for n, s in self.servidores.items():
            permitidas = s.permitidas
            lista.append({
                "id": n, "nombre": n, "tipo": s.tipo, "detalle": s.detalle,
                "descripcion": s.descripcion, "activo": n in self.activos,
                "disponible": s.disponible, "error": s.error,
                "conectado": bool(s.todas) and n in self.activos,
                "herramientas": [
                    {"nombre": h["name"], "descripcion": (h.get("description") or "").split("\n")[0][:160],
                     "lectura": bool((h.get("annotations") or {}).get("readOnlyHint")),
                     "permitida": permitidas is None or h["name"] in permitidas}
                    for h in sorted(s.todas, key=lambda h: (
                        not (permitidas is None or h["name"] in permitidas),
                        not (h.get("annotations") or {}).get("readOnlyHint"), h["name"]))],
            })
        return lista

    def cerrar(self):
        for s in self.servidores.values():
            s.cerrar()


# --------------------------------------------------- configuración e importación
def desde_formulario(tipo: str, comando: str, url: str, variables: str, descripcion: str) -> dict:
    """Convierte lo escrito en la ventana en la sección de config.toml."""
    pares = {}
    for linea in variables.splitlines():
        if "=" in linea:
            k, v = linea.split("=", 1)
        elif ":" in linea and tipo == "remoto":
            k, v = linea.split(":", 1)
        else:
            continue
        if k.strip():
            pares[k.strip()] = v.strip()
    cfg: dict = {"descripcion": descripcion.strip()}
    if tipo == "remoto":
        if not re.match(r"https?://", url.strip()):
            raise ValueError("la URL debe empezar con http:// o https://")
        cfg["url"] = url.strip()
        if pares:
            cfg["headers"] = pares
    else:
        partes = shlex.split(comando)
        if not partes:
            raise ValueError("falta el comando")
        cfg["comando"], cfg["args"] = partes[0], partes[1:]
        if pares:
            cfg["env"] = pares
    cfg["activo"] = True
    return cfg


def _leer_json(ruta: Path) -> dict:
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def importables() -> dict[str, dict]:
    """Servidores configurados en otras aplicaciones de este Mac: {"Pi · obsidian": cfg}."""
    casa = Path.home()
    encontrados: dict[str, dict] = {}

    def agregar(origen: str, nombre: str, s: dict):
        if s.get("url") and s.get("type") != "sse":
            cfg = {"url": s["url"], "headers": s.get("headers") or {}}
        elif isinstance(s.get("command"), list) and s["command"]:      # OpenCode
            cfg = {"comando": s["command"][0], "args": s["command"][1:],
                   "env": s.get("environment") or s.get("env") or {}}
        elif isinstance(s.get("command"), str):
            cfg = {"comando": s["command"], "args": list(s.get("args") or []), "env": s.get("env") or {}}
        else:
            return
        if any(v["nombre"] == nombre and v.get("url", v.get("comando")) == cfg.get("url", cfg.get("comando"))
               for v in encontrados.values()):
            return          # el mismo servidor configurado en otra app
        encontrados[f"{origen} · {nombre}"] = {"nombre": nombre, "origen": origen,
                                               **{k: v for k, v in cfg.items() if v or k in ("args",)}}

    for n, s in (_leer_json(casa / ".pi/agent/mcp.json").get("mcpServers") or {}).items():
        agregar("Pi", n, s)
    for n, s in (_leer_json(casa / ".claude.json").get("mcpServers") or {}).items():
        agregar("Claude Code", n, s)
    for n, s in (_leer_json(casa / ".config/opencode/opencode.json").get("mcp") or {}).items():
        agregar("OpenCode", n, s)
    return encontrados
