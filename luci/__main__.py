"""Luci — asistente de voz (prototipo Fase 1).

Uso:  luci [comando]

  (sin comando)      abre la interfaz (inicia el servicio si hace falta)
  configurar         modelos, clave, Luci.app e inicio automático (se hace solo la primera vez)
  actualizar         instala la última versión desde GitHub
  desinstalar        quita Luci.app y el inicio automático
  iniciar | detener | reiniciar | estado
  registro [-f]      muestra el registro del servicio (-f: seguirlo en vivo)
  silenciar [si|no]  silencia o reactiva el micrófono (sin valor: alterna)
  estado --breve     estado en una palabra (para scripts y barras de menú)

  servicio           ejecuta el servicio en primer plano (lo usa Luci.app)
  consola            motor completo en esta terminal, con texto simple (para depurar)
  dispositivos       lista los dispositivos de audio
  probar-voz [txt]   Luci dice una frase por los parlantes
  probar-oido        graba una frase y la transcribe
  probar-cerebro [p] pregunta por texto al modelo y mide la latencia
  calibrar           sugiere el umbral de interrupción para tus parlantes
  probar-turno       mide con tu voz si Smart Turn detecta bien cuándo terminas
"""

from __future__ import annotations

import argparse
import threading
import time

import numpy as np


def _cfg(args):
    from .config import cargar_config

    cfg = cargar_config()
    if getattr(args, "modelo", None):
        cfg["llm"]["modelo"] = args.modelo
    return cfg


def cmd_dispositivos(args):
    import sounddevice as sd

    print(sd.query_devices())


def cmd_probar_voz(args):
    from .audio import Reproductor
    from .config import ruta_datos
    from .tts import crear_voz

    cfg = _cfg(args)
    voz = crear_voz(cfg["tts"], ruta_datos)
    salida = Reproductor(cfg["audio"]["dispositivo"])
    salida.iniciar()
    t = time.time()
    audio, sr = voz.sintetizar(args.texto)
    print(f"Síntesis: {time.time() - t:.2f}s para {len(audio) / sr:.1f}s de audio")
    salida.encolar(audio, sr)
    while salida.sonando:
        time.sleep(0.05)
    time.sleep(0.2)
    salida.detener()


def cmd_probar_oido(args):
    from .audio import Microfono
    from .stt import Oido
    from .vad import DetectorVoz, escuchar_frase

    cfg = _cfg(args)
    print("Cargando Whisper…")
    oido = Oido(cfg["stt"]["modelo"], cfg["stt"]["idioma"], cfg["stt"].get("pista"))
    oido.calentar()
    mic = Microfono(cfg["audio"]["dispositivo"], cfg["audio"].get("canal_microfono", 1))
    mic.iniciar()
    print("Habla ahora (tienes 10 segundos para empezar)…")
    frase = escuchar_frase(mic, DetectorVoz(cfg["vad"].get("umbral", 0.5)), 10,
                           cfg["vad"]["silencio_fin_ms"], cfg["vad"]["max_frase_s"])
    mic.detener()
    if frase is None:
        print("No detecté voz. Revisa `canal_microfono` en config.toml y la ganancia de la Scarlett.")
        return
    print(f"Frase de {len(frase.audio) / 16000:.1f}s · voz {frase.duracion_voz_ms} ms "
          f"({frase.proporcion_voz:.0%}) · volumen de tu voz {frase.volumen_voz:.0f} "
          f"(filtro: volumen_min = {cfg.get('filtro', {}).get('volumen_min', 150)})")
    t = time.time()
    print(f"Texto: «{oido.transcribir(frase.audio)}»  ({time.time() - t:.2f}s)")


def cmd_probar_cerebro(args):
    from .config import cargar_persona, clave_opencode_go
    from .llm import Cerebro

    cfg = _cfg(args)
    l = cfg["llm"]
    c = Cerebro(l["base_url"], clave_opencode_go(), l["modelo"], cargar_persona(), l.get("max_tokens", 400),
               razonamiento=l.get("razonamiento", "none"))
    print(f"Modelo: {l['modelo']}\n")
    t = time.time()
    primero = None
    for delta in c.responder(args.pregunta, threading.Event()):
        primero = primero or time.time() - t
        print(delta, end="", flush=True)
    print(f"\n\nPrimera palabra: {primero or 0:.2f}s · total: {time.time() - t:.2f}s")


def cmd_calibrar(args):
    from .audio import Microfono, Reproductor
    from .config import ruta_datos
    from .tts import crear_voz
    from .vad import DetectorVoz, rms

    cfg = _cfg(args)
    a = cfg["audio"]
    mic = Microfono(a["dispositivo"], a.get("canal_microfono", 1))
    salida = Reproductor(a["dispositivo"])
    vad = DetectorVoz(cfg["vad"].get("umbral", 0.5))
    voz = crear_voz(cfg["tts"], ruta_datos)
    mic.iniciar()
    salida.iniciar()

    def medir(segundos, solo_voz=False):
        niveles = []
        fin = time.time() + segundos
        while time.time() < fin:
            c = mic.leer(timeout=0.2)
            if c is not None and (not solo_voz or vad.es_voz(c)):
                niveles.append(rms(c))
        return np.array(niveles or [0.0])

    print("1/3  Silencio: no hables durante 3 segundos…")
    time.sleep(0.5); mic.vaciar()
    ruido = medir(3)
    print(f"     ruido de fondo ≈ {np.percentile(ruido, 90):.0f}")

    print("2/3  Luci va a hablar; quédate en silencio…")
    audio, sr = voz.sintetizar("Estoy midiendo cuánto de mi propia voz llega a tu micrófono. "
                               "Por favor, quédate en silencio mientras termino esta frase un poco larga.")
    mic.vaciar()
    salida.encolar(audio, sr)
    eco = medir(len(audio) / sr + 0.3, solo_voz=True)
    eco_max = float(np.percentile(eco, 95))
    print(f"     eco de Luci ≈ {eco_max:.0f}")

    print("3/3  Ahora habla tú normalmente durante 4 segundos, desde donde sueles estar…")
    time.sleep(0.3); mic.vaciar()
    tu = medir(4, solo_voz=True)
    tu_nivel = float(np.percentile(tu, 50))
    print(f"     tu voz ≈ {tu_nivel:.0f}")
    mic.detener(); salida.detener()

    print()
    if tu_nivel > eco_max * 1.5:
        sugerido = int((eco_max * 1.3 + tu_nivel * 0.7) / 2)
        print(f"Umbral sugerido: umbral_rms = {sugerido}  (ponlo en [interrupcion] de config.toml)")
    else:
        print("Tu voz y el eco de Luci llegan con volumen parecido: con estos parlantes la interrupción\n"
              "por volumen no será fiable. Opciones: usar audífonos, bajar el volumen de los parlantes,\n"
              "alejarlos del micrófono, o desactivar la interrupción (activa = false) hasta tener\n"
              "cancelación de eco.")


def cmd_probar_turno(args):
    """Mide Smart Turn con tu voz: en cada pausa, ¿cree que terminaste?"""
    from .audio import FRAME, Microfono
    from .turno import DetectorTurno
    from .vad import DetectorVoz, escuchar_frase

    cfg = _cfg(args)
    v = cfg["vad"]
    pausa = max(1, v.get("pausa_turno_ms", 200) // 32)
    umbral = v.get("umbral_turno", 0.5)
    print("Cargando…")
    turno = DetectorTurno()
    mic = Microfono(cfg["audio"]["dispositivo"], cfg["audio"].get("canal_microfono", 1))
    vad = DetectorVoz(v.get("umbral", 0.5))
    mic.iniciar()
    print("\nHabla como lo harías con Luci. Di frases completas y también frases con pausas para")
    print("pensar a mitad (p. ej. «Luci, quería preguntarte… sobre el clima de mañana»).")
    print("Deja ~2 s de silencio entre frases. Ctrl+C para terminar.\n")
    aciertos = total = 0
    try:
        while True:
            frase = escuchar_frase(mic, vad, espera_s=None, silencio_fin_ms=1500, max_frase_s=20)
            if frase is None:
                continue
            audio = frase.audio
            cuadros = [audio[k * FRAME:(k + 1) * FRAME] for k in range(len(audio) // FRAME)]
            marcas_vad = DetectorVoz(v.get("umbral", 0.5))
            voz = [marcas_vad.es_voz(c) for c in cuadros]
            ultimo = max((i for i, x in enumerate(voz) if x), default=0)
            # pausas internas: silencio de `pausa` cuadros seguido de más voz
            internas, silencio = [], 0
            for i, x in enumerate(voz[:ultimo + 1]):
                silencio = 0 if x else silencio + 1
                if silencio == pausa:
                    internas.append(i)
            print(f"Frase de {len(audio) / 16000:.1f} s")
            for i in internas:
                p = turno.probabilidad_fin(audio[:(i + 1) * FRAME])
                ok = p < umbral
                aciertos += ok; total += 1
                print(f"   pausa a mitad ({(i + 1) * 0.032:.1f} s): p={p:.2f}  "
                      f"{'✔ seguiría esperando' if ok else '✘ te habría cortado'}")
            fin = min(len(audio), (ultimo + 1 + pausa) * FRAME)
            p = turno.probabilidad_fin(audio[:fin])
            ok = p >= umbral
            aciertos += ok; total += 1
            print(f"   al terminar:                p={p:.2f}  "
                  f"{'✔ respondería ya' if ok else '✘ esperaría de más'}\n")
    except KeyboardInterrupt:
        pass
    finally:
        mic.detener()
    if total:
        print(f"\nAciertos: {aciertos}/{total} con umbral {umbral}.")


def cmd_conversar(args):
    from .cliente import servicio_activo
    from .config import PLIST

    if not servicio_activo():
        from . import servicio

        if PLIST.exists():
            servicio.iniciar(esperar=False)
        else:
            # Primera vez: se configura todo (con confirmación).
            print("Luci todavía no está configurada en este Mac.")
            respuesta = input("¿Configurarla ahora? [S/n] ").strip().lower()
            if respuesta not in ("", "s", "si", "sí", "y", "yes"):
                print("Cuando quieras: luci configurar")
                return
            servicio.configurar()
    from .tui import ejecutar_tui

    ejecutar_tui()


def cmd_servicio(args):
    from .servidor import ejecutar_servicio

    ejecutar_servicio()


def cmd_silenciar(args):
    """Silencia o reactiva el micrófono (sin argumento: alterna)."""
    from .cliente import consultar
    from .estados import SILENCIADO

    estado = consultar()
    if estado is None:
        raise SystemExit("Luci no está corriendo.")
    if estado.get("estado") in ("cargando", "error"):
        raise SystemExit(f"Luci está {estado['estado'] == 'cargando' and 'cargando' or 'con un error'}; "
                         "inténtalo en unos segundos.")
    silenciada = estado.get("estado") == SILENCIADO
    quiero = {"si": True, "sí": True, "no": False}.get(args.valor or "", not silenciada)
    if quiero != silenciada:
        consultar("silenciar")
        time.sleep(0.3)
        nuevo = consultar()
        silenciada = bool(nuevo) and nuevo.get("estado") == SILENCIADO
    print("silenciada" if silenciada else "escuchando")


def cmd_estado(args):
    if args.breve:
        # Una palabra, para scripts: espera, conversando, pensando, hablando,
        # silenciado, cargando, error o apagada.
        from .cliente import consultar

        estado = consultar()
        print(estado.get("estado", "apagada") if estado else "apagada")
        return
    from . import servicio

    servicio.estado()


def _servicio(nombre):
    def cmd(args):
        from . import servicio

        if nombre == "registro":
            servicio.registro(seguir=args.seguir)
        else:
            getattr(servicio, nombre)()
    return cmd


def cmd_consola(args):
    from .motor import Motor

    iconos = {"usuario": "🗣️  Tú:", "luci": "🪲 Luci:", "info": "ℹ️ ", "debug": "  ·", "oido": "  ·", "error": "❌"}
    m = Motor(_cfg(args), lambda ev: print(f"[{time.strftime('%H:%M:%S')}] {iconos[ev.tipo]} {ev.texto}",
                                         flush=True))
    try:
        m.ejecutar()
    except KeyboardInterrupt:
        m.detener()


def main():
    p = argparse.ArgumentParser(prog="luci", description="Luci — asistente de voz (Fase 1)")
    p.add_argument("--modelo", help="modelo de OpenCode Go (sobrescribe config.toml)")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("conversar")
    sub.add_parser("servicio")
    sub.add_parser("consola")
    for nombre in ("configurar", "actualizar", "desinstalar", "iniciar", "detener", "reiniciar"):
        sub.add_parser(nombre)
    e = sub.add_parser("estado")
    e.add_argument("--breve", action="store_true", help="una sola palabra, para scripts")
    si = sub.add_parser("silenciar", help="silencia o reactiva el micrófono")
    si.add_argument("valor", nargs="?", choices=["si", "sí", "no"], help="sin valor: alterna")
    r = sub.add_parser("registro")
    r.add_argument("-f", dest="seguir", action="store_true", help="seguir el registro en vivo")
    sub.add_parser("dispositivos")
    v = sub.add_parser("probar-voz")
    v.add_argument("texto", nargs="?", default="Hola, soy Luci. Mis engranajes están listos para conversar contigo.")
    sub.add_parser("probar-oido")
    c = sub.add_parser("probar-cerebro")
    c.add_argument("pregunta", nargs="?", default="¿Por qué el cielo es azul?")
    sub.add_parser("calibrar")
    sub.add_parser("probar-turno")
    args = p.parse_args()
    comandos = {
        None: cmd_conversar, "conversar": cmd_conversar, "servicio": cmd_servicio,
        "consola": cmd_consola, "dispositivos": cmd_dispositivos,
        "probar-voz": cmd_probar_voz, "probar-oido": cmd_probar_oido,
        "probar-cerebro": cmd_probar_cerebro, "calibrar": cmd_calibrar, "probar-turno": cmd_probar_turno,
    }
    for nombre in ("configurar", "actualizar", "desinstalar", "iniciar", "detener", "reiniciar", "registro"):
        comandos[nombre] = _servicio(nombre)
    comandos["estado"] = cmd_estado
    comandos["silenciar"] = cmd_silenciar
    comandos[args.cmd](args)


if __name__ == "__main__":
    main()
