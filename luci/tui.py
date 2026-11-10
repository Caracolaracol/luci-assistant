"""Interfaz de terminal de Luci (Textual). Se conecta al servicio por un socket local,
así que funciona desde cualquier terminal (Ghostty, herdr, SSH…) sin necesitar el micrófono."""

from __future__ import annotations

from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, RichLog, Static

from . import estados as E
from .cliente import Cliente

DESCONECTADO = "desconectado"

_ESTADOS = {
    DESCONECTADO:  ("⛔", "Servicio detenido", "red"),
    E.ERROR:       ("✖", "Error", "red"),
    E.CARGANDO:    ("⚙️", "Cargando…", "grey62"),
    E.ESPERA:      ("💤", "En espera", "grey62"),
    E.CONVERSANDO: ("👂", "Escuchándote", "green"),
    E.PENSANDO:    ("⚙️", "Pensando…", "yellow"),
    E.HABLANDO:    ("🗣️", "Hablando", "dark_orange"),
    E.SILENCIADO:  ("🔇", "Micrófono silenciado", "red"),
}
_AYUDA = {
    DESCONECTADO:  "Inícialo con «luci iniciar».\nReintentando conexión…",
    E.ERROR:       "Revisa el mensaje en la conversación y «luci registro».",
    E.CARGANDO:    "Preparando audio y modelos.",
    E.ESPERA:      "Escucho en local, pero solo respondo si me llamas: «Luci, …».",
    E.CONVERSANDO: "Puedes seguir sin decir mi nombre.",
    E.PENSANDO:    "Puedes interrumpirme hablando.",
    E.HABLANDO:    "Háblame para interrumpirme.",
    E.SILENCIADO:  "Pulsa m para reactivar el micrófono.",
}


class LuciApp(App):
    TITLE = "Luci"
    SUB_TITLE = "asistente de voz"
    CSS = """
    Screen { layout: vertical; }
    #cuerpo { height: 1fr; }
    #lateral { width: 34; padding: 1 2; border-right: tall $panel; }
    #estado { height: 3; content-align: left middle; text-style: bold; }
    #ayuda { color: $text-muted; height: 4; }
    #medidor { height: 2; margin-top: 1; }
    #datos { margin-top: 1; color: $text-muted; }
    #chat { padding: 0 1; }
    """
    BINDINGS = [
        Binding("m", "enviar('silenciar')", "Silenciar"),
        Binding("c", "enviar('conversar')", "Hablar sin nombre"),
        Binding("n", "enviar('nueva')", "Nueva conversación"),
        Binding("t", "enviar('modelo')", "Cambiar modelo"),
        Binding("d", "detalles", "Detalles"),
        Binding("q", "quit", "Salir"),
    ]

    def __init__(self):
        super().__init__()
        self.detalles = True
        self._ultimo_tipo = ""
        self._estado: dict = {"estado": DESCONECTADO}
        self.cliente = Cliente(self._desde_hilo)

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="cuerpo"):
            with Vertical(id="lateral"):
                yield Static(id="estado")
                yield Static(id="ayuda")
                yield Static(id="medidor")
                yield Static(id="datos")
            yield RichLog(id="chat", wrap=True, markup=True, highlight=False)
        yield Footer()

    def on_mount(self):
        self.cliente.iniciar()
        self.set_interval(0.1, self._refrescar)
        self._refrescar()

    def on_unmount(self):
        self.cliente.cerrar()

    # ------------------------------------------------------------ mensajes
    def _desde_hilo(self, msg: dict):
        try:
            self.call_from_thread(self._recibir, msg)
        except RuntimeError:        # la app se está cerrando
            pass

    def _recibir(self, msg: dict):
        t = msg.get("t")
        if t == "estado":
            self._estado = msg
        elif t == "evento":
            self._mostrar(msg)
        elif t == "historial":
            self.query_one("#chat", RichLog).clear()
            self._ultimo_tipo = ""
            for ev in msg.get("eventos", []):
                self._mostrar(ev)
        elif t == "conexion":
            if not msg["conectado"]:
                self._estado = {"estado": DESCONECTADO}

    def _mostrar(self, ev: dict):
        chat = self.query_one("#chat", RichLog)
        tipo, t = ev.get("tipo"), escape(ev.get("texto", ""))
        if tipo == "usuario":
            chat.write(Text(""))
            chat.write(f"[bold cyan]Tú:[/] {t}")
        elif tipo == "luci":
            if self._ultimo_tipo == "luci":
                chat.write(f"       {t}")
            else:
                chat.write(f"[bold dark_orange]Luci:[/] {t}")
        elif tipo == "error":
            chat.write(f"[bold red]✖ {t}[/]")
        elif tipo == "info":
            chat.write(f"[italic grey62]{t}[/]")
        elif tipo == "debug":
            if not self.detalles:
                return
            chat.write(f"[grey46]  · {t}[/]")
        if tipo in ("usuario", "luci"):
            self._ultimo_tipo = tipo

    # ----------------------------------------------------------- pantalla
    def _refrescar(self):
        e = self._estado
        nombre_estado = e.get("estado", DESCONECTADO)
        icono, nombre, color = _ESTADOS.get(nombre_estado, ("?", nombre_estado, "white"))
        self.query_one("#estado", Static).update(Text(f"{icono}  {nombre}", style=f"bold {color}"))
        ayuda = _AYUDA.get(nombre_estado, "")
        if nombre_estado == E.CONVERSANDO:
            ayuda += f"\nVuelvo a espera en {e.get('restante', 0):.0f} s."
        self.query_one("#ayuda", Static).update(ayuda)
        n = int(e.get("nivel", 0) * 24)
        barra = Text("micrófono\n", style="dim")
        barra.append("█" * n, style="green" if n < 18 else "yellow")
        barra.append("·" * (24 - n), style="grey35")
        self.query_one("#medidor", Static).update(barra)
        if nombre_estado == DESCONECTADO:
            datos = ""
        else:
            datos = (f"modelo: {e.get('modelo', '?')}\nvoz a texto: {e.get('stt', '?')}\n"
                     f"detalles: {'sí' if self.detalles else 'no'}")
        self.query_one("#datos", Static).update(datos)

    # ------------------------------------------------------------ acciones
    def action_enviar(self, cmd: str):
        if not self.cliente.enviar(cmd):
            self.notify("El servicio de Luci no está corriendo.", severity="warning")

    def action_detalles(self):
        self.detalles = not self.detalles


def ejecutar_tui():
    LuciApp().run()
