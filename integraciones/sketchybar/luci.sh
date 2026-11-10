#!/bin/sh
# Luci en SketchyBar: muestra su estado y permite controlarla.
#
#   clic           silenciar / reactivar el micrófono
#   clic derecho   abrir la interfaz de Luci en una terminal
#
# El servicio de Luci avisa de cada cambio con el evento `luci_estado` (variable ESTADO),
# así que no hace falta consultar a cada rato. Ver integraciones/sketchybar/README.md.

LUCI="${LUCI:-$HOME/.local/bin/luci}"
# Terminal para la interfaz (clic derecho): Ghostty si está instalado; si no, Terminal.
abrir_interfaz() {
    if [ -d "/Applications/Ghostty.app" ]; then
        open -na Ghostty --args -e "$LUCI"
    else
        osascript -e "tell application \"Terminal\" to do script \"$LUCI\"" \
                  -e 'tell application "Terminal" to activate' >/dev/null
    fi
}

if [ "$SENDER" = "mouse.clicked" ]; then
    if [ "$BUTTON" = "right" ]; then
        abrir_interfaz
    else
        "$LUCI" silenciar >/dev/null 2>&1    # el servicio avisará el nuevo estado
    fi
    exit 0
fi

ESTADO="${ESTADO:-$("$LUCI" estado --breve 2>/dev/null || echo apagada)}"

# Íconos (Nerd Font, Material Design): mariquita, micrófono tachado, bicho en contorno.
MARIQUITA="󰠭"   # nf-md-ladybug
SILENCIO="󰍭"    # nf-md-microphone_off
APAGADA="󰨯"     # nf-md-bug_outline

case "$ESTADO" in
    espera)      ICONO="$MARIQUITA"; COLOR=0xff7f849c; TEXTO="Luci" ;;
    conversando) ICONO="$MARIQUITA"; COLOR=0xffa6e3a1; TEXTO="te escucho" ;;
    pensando)    ICONO="$MARIQUITA"; COLOR=0xfff9e2af; TEXTO="pensando" ;;
    hablando)    ICONO="$MARIQUITA"; COLOR=0xfffab387; TEXTO="hablando" ;;
    silenciado)  ICONO="$SILENCIO";  COLOR=0xfff38ba8; TEXTO="silenciada" ;;
    cargando)    ICONO="$MARIQUITA"; COLOR=0xff585b70; TEXTO="cargando" ;;
    error)       ICONO="$MARIQUITA"; COLOR=0xfff38ba8; TEXTO="error" ;;
    *)           ICONO="$APAGADA";   COLOR=0xff585b70; TEXTO="apagada" ;;
esac

sketchybar --set "$NAME" icon="$ICONO" icon.color="$COLOR" label="$TEXTO"
