# Luci 🪲 — asistente de voz

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
  detecta).
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
| `q` | Cerrar la interfaz (Luci sigue escuchando) |

### Cuándo habla y cuándo no

- **En espera** transcribe en local lo que oye, pero solo responde si la llamas: «Luci, …».
- **«Luci»** u «Oye Luci» a secas: responde con un pitido y queda escuchando.
- **Durante la conversación**, tras cada respuesta hay unos segundos (`ventana_s`) para seguir
  sin nombrarla. En ese tiempo el modelo decide si la frase va dirigida a Luci; si no, calla.
- **«Gracias, Luci»**, «Luci, silencio» o «eso es todo» la devuelven a espera.
- **Para interrumpirla**, háblale mientras responde.

## Cómo está organizada

- **Servicio** (`~/Applications/Luci.app`, se inicia al iniciar sesión): tiene el permiso de
  micrófono y hace todo el trabajo: escuchar, transcribir, pensar y hablar.
- **Interfaz** (`luci`): aplicación de terminal que muestra el estado y la conversación.
  Funciona desde cualquier terminal (Ghostty, tmux, herdr, SSH…) porque no usa el micrófono:
  se conecta al servicio por un socket local.

## Comandos

```bash
luci estado               # ¿configurada? ¿corriendo?
luci iniciar | detener | reiniciar
luci registro [-f]        # registro del servicio
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
- Detección del fin de turno por entonación (Smart Turn) para responder antes.
- Herramientas: consultar memorias de proyectos (Engram), notas, calendario…
- Fase de hardware: el escarabajo.

## Licencia

[GPL-3.0](LICENSE). Luci usa Piper, que también se distribuye bajo GPL-3.0.
