# Luci en SketchyBar

Ítem para [SketchyBar](https://github.com/FelixKratz/SketchyBar) que muestra el estado de
Luci y permite controlarla:

- **clic**: silenciar / reactivar el micrófono
- **clic derecho**: abrir la interfaz de Luci (Ghostty si está instalado; si no, Terminal)

Los íconos son de una [Nerd Font](https://www.nerdfonts.com/) (Material Design), así que tu
`icon.font` debe ser una Nerd Font.

## Instalación

1. Copia el script:

   ```bash
   mkdir -p ~/.config/sketchybar/plugins
   curl -fsSL -o ~/.config/sketchybar/plugins/luci.sh \
     https://raw.githubusercontent.com/caracolaracol/luci-assistant/main/integraciones/sketchybar/luci.sh
   chmod +x ~/.config/sketchybar/plugins/luci.sh
   ```

2. Añade a tu `sketchybarrc`:

   ```bash
   sketchybar --add event luci_estado
   sketchybar --add item luci right                                   \
              --set luci script="$CONFIG_DIR/plugins/luci.sh"         \
                         update_freq=60                               \
              --subscribe luci luci_estado mouse.clicked system_woke
   ```

El servicio de Luci dispara el evento `luci_estado` en cada cambio de estado, así que el
ítem se actualiza al instante. `update_freq=60` solo sirve de respaldo, por ejemplo si
SketchyBar se reinicia. Para desactivar los avisos: `sketchybar = false` en la sección
`[integraciones]` de `~/.config/luci/config.toml`.
