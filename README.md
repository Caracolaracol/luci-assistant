<p align="center">
  <img src="luci/app/marca/luci-1024.png" width="112" alt="Ícono de Luci: una luciérnaga con el farol encendido">
</p>

# Luci — asistente de voz

Luci es una asistente de voz en español para macOS. Escucha siempre, responde cuando la
llamas por su nombre («Luci, …») y se queda callada cuando hablas con otras personas.
Su nombre viene de la luciérnaga, el escarabajo que se comunica con luz: Luci es el cerebro
de un futuro asistente físico con forma de escarabajo steampunk.

```
Micrófono → detector de voz (Silero) → Whisper local (MLX)
    → ¿me llamaron? ──no──→ se ignora (nada sale del Mac)
          │ sí
    → modelo de lenguaje vía OpenCode Go (DeepSeek / MiMo) → voz local (Piper) → parlantes
```

Todo el audio se procesa en el Mac. Solo el texto de las frases dirigidas a Luci se envía al
modelo de lenguaje.

## Instalación

```bash
curl -fsSL https://raw.githubusercontent.com/caracolaracol/luci-assistant/main/instalar.sh | sh
```

El instalador instala [uv](https://docs.astral.sh/uv/) si hace falta, el comando `luci`, los
modelos de voz, pide tu clave de OpenCode Go y deja Luci iniciándose al iniciar sesión.
**Cuando macOS pregunte si «Luci» puede usar el micrófono, pulsa Permitir.** La primera vez
también descarga Whisper (unos 500 MB).

### Requisitos

- macOS 13 o superior, Mac con Apple Silicon.
- Herramientas de línea de comandos de Xcode (`xcode-select --install`; el instalador lo
  detecta): se usan para compilar `Luci.app`.
- Micrófono y parlantes (probado con una interfaz Focusrite Scarlett 2i2).
- Una suscripción a [OpenCode Go](https://opencode.ai/docs/go) y su clave de API.

### Instalación manual

```bash
uv tool install git+https://github.com/caracolaracol/luci-assistant
luci        # la primera vez ofrece configurarla
```

## Uso

```bash
luci
```

| Tecla | Acción |
|---|---|
| `m` | Silenciar / reactivar el micrófono |
| `c` | Abrir conversación sin decir el nombre |
| `n` | Nueva conversación (olvida lo hablado) |
| `t` | Cambiar de modelo |
| `d` | Mostrar u ocultar detalles (qué ignoró y por qué, tiempos) |
| `q` | Cerrar la interfaz (Luci sigue escuchando; para apagarla: `luci detener`) |

### Cuándo habla y cuándo no

- **En espera** transcribe en local lo que oye, pero solo responde si la llamas: «Luci, …».
- **«Luci»** u «Oye Luci» a secas: responde con un pitido y queda escuchando.
- **Durante la conversación**, tras cada respuesta hay unos segundos (`ventana_s`) para seguir
  sin nombrarla. En ese tiempo el modelo decide si la frase va dirigida a Luci; si no, calla.
- **«Gracias, Luci»**, «Luci, silencio» o «eso es todo» la devuelven a espera.
- **Para interrumpirla**, háblale mientras responde.

## Barra de menú

Luci muestra la cola de una luciérnaga en la barra de menú de macOS mientras está activa: el
farol en contorno en espera, relleno cuando te escucha, piensa o habla, y un micrófono tachado
si está silenciada. Para ocultar el ícono: `icono_menu = false` en `[integraciones]`.

- **Clic**: abre la ventana de ajustes.
- **Clic derecho**: menú rápido (silenciar, hablarle sin decir su nombre, nueva conversación,
  abrir la interfaz, detenerla).

### Ajustes

La ventana muestra el estado de Luci, botones para silenciarla o empezar de nuevo, y permite
elegir sin reiniciar:

- **Transcripción**: el modelo de Whisper (MLX). Del más liviano (Small · 8 bits, ~300 MB)
  al más preciso (Large v3 Turbo, ~1,6 GB).
- **Voz**: voces de Piper en español (México, Argentina, España) o las voces de macOS. Con el
  botón del parlante la escuchas antes de elegirla.
- **Modelo de lenguaje**: cualquiera de los que ofrece OpenCode Go. Los marcados como
  **Respaldo** se usan, en orden, si el modelo elegido está saturado o no responde.
- **Ajustes del modelo**: nivel de razonamiento y clave de OpenCode Go.

- **Herramientas**: activa o desactiva las herramientas que Luci puede consultar (ver abajo).

Los modelos descargados que no estén en uso se pueden borrar con el botón de la papelera.

Los modelos que faltan aparecen con su tamaño. Al elegir uno, Luci pide confirmación, lo
descarga mostrando el avance y lo usa en cuanto termina. La elección se guarda en
`config.toml`.

Luci reutiliza los modelos que ya estén en tu Mac: Whisper MLX desde la caché de Hugging
Face (`~/.cache/huggingface/hub`), compartida con otras herramientas, y las voces de Piper
desde `~/Library/Application Support/Luci/models/piper`. Los modelos GGUF (whisper.cpp,
Handy, Mantis) usan otro motor y no son compatibles.

¿Usas [SketchyBar](https://github.com/FelixKratz/SketchyBar)? Hay un ítem listo en
[`integraciones/sketchybar`](integraciones/sketchybar/README.md).

## Herramientas (MCP)

Luci puede consultar servidores [MCP](https://modelcontextprotocol.io), los mismos que usan
Claude Code, OpenCode o Pi. Viene con [Engram](https://github.com/Gentleman-Programming/engram),
si está instalado: pregúntale «Luci, ¿qué decidimos sobre la paleta?» y busca en tus memorias
antes de responder.

Desde la ventana de ajustes, sección **Herramientas (MCP)** › **Agregar…**:

- **Importar** los servidores que ya tengas configurados en Pi, Claude Code u OpenCode, con
  sus claves.
- **Agregar a mano** un servidor local (un comando, como `npx -y …`, con sus variables de
  entorno) o remoto (una URL, con sus cabeceras).

Ningún servidor necesita código propio en Luci. Al agregar uno, solo quedan permitidas las
herramientas que el servidor declara de **solo lectura**: por voz no hay confirmación antes
de actuar. Con las casillas de cada servidor eliges qué más puede usar, y en «Cuándo usarlo»
le explicas al modelo para qué sirve. Si un servidor no responde (por ejemplo, Obsidian
cerrado), Luci lo reintenta cada minuto.

Todo queda en `config.toml`, que se puede editar a mano:

```toml
[herramientas.github]
comando = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
env = { GITHUB_TOKEN = "…" }
permitidas = ["search_issues", "get_issue"]
descripcion = "Mis repositorios: úsalo cuando pregunte por issues o PRs."
activo = true

[herramientas.notas]
url = "https://127.0.0.1:27124/mcp/"
headers = { Authorization = "Bearer …" }
```

Lo que devuelve una herramienta se envía al modelo de lenguaje junto con tu pregunta.

## Cómo está organizada

- **Servicio** (`~/Applications/Luci.app`, se inicia al iniciar sesión): tiene el permiso de
  micrófono, muestra el ícono de la barra de menú y hace todo el trabajo: escuchar,
  transcribir, pensar y hablar.
- **Interfaz** (`luci`): aplicación de terminal que muestra el estado y la conversación.
  Funciona desde cualquier terminal (Ghostty, tmux, herdr, SSH…) porque no usa el micrófono:
  se conecta al servicio por un socket local.

## Comandos

```bash
luci estado               # ¿configurada? ¿corriendo?
luci iniciar | detener | reiniciar
luci registro [-f]        # registro del servicio
luci silenciar [si|no]    # silencia o reactiva el micrófono (sin valor: alterna)
luci estado --breve       # estado en una palabra, para scripts
luci actualizar           # última versión desde GitHub
luci configurar           # rehace la configuración (modelos, clave, Luci.app)
luci desinstalar          # quita Luci.app y el inicio automático
```

Pruebas y ajustes (usan el micrófono desde la terminal, que necesita su propio permiso):

```bash
luci dispositivos         # dispositivos de audio disponibles
luci probar-voz           # Luci habla por los parlantes
luci probar-oido          # di una frase: transcripción y volumen de tu voz
luci probar-cerebro       # pregunta de texto al modelo, con latencia
luci calibrar             # sugiere el umbral de interrupción
```

## Configuración

| Archivo | Contenido |
|---|---|
| `~/.config/luci/config.toml` | Dispositivo de audio, modelos, voz, ventana de conversación, filtros de ruido, interrupción |
| `~/.config/luci/persona.md` | Personalidad de Luci |
| `~/.config/luci/.env` | Clave de OpenCode Go (`OPENCODE_GO_API_KEY=…`) |
| `~/Library/Application Support/Luci/` | Modelos de voz y detector de voz |
| `~/Library/Logs/Luci/luci.log` | Registro del servicio |

Tras editar la configuración: `luci reiniciar`. Revisa sobre todo `dispositivo` (nombre de tu
interfaz de audio) y `canal_microfono`. Las reglas para decidir si le hablan a ella están en
`luci/llm.py` (`REGLAS_VOZ`).

## Problemas frecuentes

- **No me escucha.** Mira el medidor del micrófono en la interfaz. Si aparece el aviso de
  «silencio absoluto», macOS no le dio permiso: revisa Ajustes del Sistema › Privacidad y
  seguridad › Micrófono. Si el medidor se mueve pero ignora lo que dices, activa los detalles
  (`d`) para ver el motivo; con `luci probar-oido` ves el volumen de tu voz frente a
  `volumen_min`.
- **Volvió a pedir permiso de micrófono.** El permiso queda asociado a la firma de `Luci.app`,
  que se regenera con cada `luci configurar`.
- **Se interrumpe sola.** Sin cancelación de eco, la interrupción depende del volumen
  (`umbral_rms`): usa `luci calibrar`, baja el volumen de los parlantes o usa audífonos.

## Desarrollo

```bash
git clone https://github.com/caracolaracol/luci-assistant && cd luci-assistant
uv tool install --editable .     # el comando `luci` usa este código directamente
luci reiniciar                   # tras cambiar el código
luci consola                     # motor completo en la terminal, sin servicio
```

## Componentes

- [mlx-whisper](https://github.com/ml-explore/mlx-examples) — voz a texto (`whisper-small`)
- [Silero VAD](https://github.com/snakers4/silero-vad) — detección de voz
- [Piper](https://github.com/OHF-Voice/piper1-gpl) — texto a voz (`es_MX-claude-high`)
- [Textual](https://textual.textualize.io/) — interfaz de terminal
- [OpenCode Go](https://opencode.ai/docs/go) — modelo de lenguaje

## Pendiente

- Cancelación de eco real (hoy la interrupción se basa en el volumen).
- Validar Smart Turn (fin de turno por entonación) con voces reales y activarlo por defecto.
- Más herramientas: notas, calendario…
- Fase de hardware: el escarabajo.

## Licencia

[GPL-3.0](LICENSE). Luci usa Piper, que también se distribuye bajo GPL-3.0.
