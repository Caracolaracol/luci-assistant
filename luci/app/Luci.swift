// Luci.app — ícono en la barra de menú y lanzador del servicio.
//
// macOS concede el permiso de micrófono a aplicaciones (.app), no a scripts sueltos. Esta
// app es el "cuerpo" de Luci: arranca el servicio en Python como proceso hijo (que hereda
// el permiso de la app) y muestra el estado en la barra de menú, conectándose al servicio
// por el mismo socket que usa la interfaz de terminal.
//
// La ruta del intérprete de Python está en Contents/Resources/python (la escribe
// `luci configurar`). Variables de entorno para pruebas:
//   LUCI_SIN_SERVICIO=1   no arranca el servicio (solo el ícono, conectado a uno existente)

import Cocoa

let rutaSocket = NSHomeDirectory() + "/Library/Application Support/Luci/luci.sock"
let rutaConfig = NSHomeDirectory() + "/.config/luci/config.toml"

final class Conexion {
    private var fd: Int32 = -1
    private let lock = NSLock()
    var alEstado: ((String) -> Void)?

    func iniciar() {
        Thread.detachNewThread { [weak self] in self?.bucle() }
    }

    func enviar(_ cmd: String) {
        lock.lock(); defer { lock.unlock() }
        guard fd >= 0, let datos = "{\"cmd\": \"\(cmd)\"}\n".data(using: .utf8) else { return }
        _ = datos.withUnsafeBytes { write(fd, $0.baseAddress, datos.count) }
    }

    private func conectar() -> Int32 {
        let s = socket(AF_UNIX, SOCK_STREAM, 0)
        guard s >= 0 else { return -1 }
        var dir = sockaddr_un()
        dir.sun_family = sa_family_t(AF_UNIX)
        let capacidad = MemoryLayout.size(ofValue: dir.sun_path)
        _ = withUnsafeMutablePointer(to: &dir.sun_path) {
            $0.withMemoryRebound(to: CChar.self, capacity: capacidad) { strncpy($0, rutaSocket, capacidad - 1) }
        }
        let ok = withUnsafePointer(to: &dir) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(s, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        if ok != 0 { close(s); return -1 }
        return s
    }

    private func bucle() {
        while true {
            let s = conectar()
            if s < 0 {
                alEstado?("apagada")
                Thread.sleep(forTimeInterval: 1)
                continue
            }
            lock.lock(); fd = s; lock.unlock()
            var buffer = Data()
            var bloque = [UInt8](repeating: 0, count: 65536)
            while true {
                let n = read(s, &bloque, bloque.count)
                if n <= 0 { break }
                buffer.append(contentsOf: bloque[0..<n])
                while let i = buffer.firstIndex(of: 0x0A) {
                    let linea = buffer[buffer.startIndex..<i]
                    buffer.removeSubrange(buffer.startIndex...i)
                    if let obj = try? JSONSerialization.jsonObject(with: linea) as? [String: Any],
                       obj["t"] as? String == "estado", let estado = obj["estado"] as? String {
                        alEstado?(estado)
                    }
                }
            }
            lock.lock(); close(s); fd = -1; lock.unlock()
            alEstado?("apagada")
            Thread.sleep(forTimeInterval: 0.5)
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var item: NSStatusItem?
    private var servicio: Process?
    private var saliendo = false
    private let conexion = Conexion()
    private var estado = ""
    private let lineaEstado = NSMenuItem(title: "Luci: conectando…", action: nil, keyEquivalent: "")
    private let opcionSilenciar = NSMenuItem(title: "Silenciar micrófono", action: #selector(silenciar), keyEquivalent: "")
    private let opcionConversar = NSMenuItem(title: "Hablarle sin decir su nombre", action: #selector(conversar), keyEquivalent: "")
    private let opcionNueva = NSMenuItem(title: "Nueva conversación", action: #selector(nueva), keyEquivalent: "")

    func applicationDidFinishLaunching(_ aviso: Notification) {
        if ProcessInfo.processInfo.environment["LUCI_SIN_SERVICIO"] == nil {
            iniciarServicio()
        }
        if iconoActivado() {
            crearIcono()
        }
        conexion.alEstado = { [weak self] nuevo in
            DispatchQueue.main.async { self?.actualizar(nuevo) }
        }
        conexion.iniciar()

        // launchd detiene el servicio con SIGTERM: se cierra ordenadamente.
        signal(SIGTERM, SIG_IGN)
        let fuente = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        fuente.setEventHandler { NSApp.terminate(nil) }
        fuente.resume()
        senalTerm = fuente
    }
    private var senalTerm: DispatchSourceSignal?

    func applicationWillTerminate(_ aviso: Notification) {
        saliendo = true
        if let p = servicio, p.isRunning {
            p.terminate()
            p.waitUntilExit()
        }
    }

    // ------------------------------------------------------------- servicio
    private func iniciarServicio() {
        guard let recursos = Bundle.main.resourcePath,
              let python = try? String(contentsOfFile: recursos + "/python", encoding: .utf8)
                .trimmingCharacters(in: .whitespacesAndNewlines),
              !python.isEmpty else {
            FileHandle.standardError.write("luci: falta Contents/Resources/python (ejecuta `luci configurar`)\n".data(using: .utf8)!)
            exit(1)
        }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = ["-m", "luci", "servicio"]
        p.terminationHandler = { [weak self] proceso in
            // Si el servicio termina solo, la app también: launchd la reinicia si fue un fallo.
            guard self?.saliendo == false else { return }
            DispatchQueue.main.async { exit(proceso.terminationStatus == 0 ? 0 : 1) }
        }
        do {
            try p.run()
            servicio = p
        } catch {
            FileHandle.standardError.write("luci: no pude iniciar \(python): \(error)\n".data(using: .utf8)!)
            exit(1)
        }
    }

    // ---------------------------------------------------------------- ícono
    private func iconoActivado() -> Bool {
        // `icono_menu = false` en [integraciones] de config.toml oculta el ícono.
        guard let texto = try? String(contentsOfFile: rutaConfig, encoding: .utf8) else { return true }
        for linea in texto.split(separator: "\n") {
            let l = linea.replacingOccurrences(of: " ", with: "")
            if l.hasPrefix("icono_menu=") { return !l.hasPrefix("icono_menu=false") }
        }
        return true
    }

    private func crearIcono() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        let menu = NSMenu()
        lineaEstado.isEnabled = false
        menu.addItem(lineaEstado)
        menu.addItem(.separator())
        for opcion in [opcionSilenciar, opcionConversar, opcionNueva] {
            opcion.target = self
            menu.addItem(opcion)
        }
        menu.addItem(.separator())
        let abrir = NSMenuItem(title: "Abrir interfaz", action: #selector(abrirInterfaz), keyEquivalent: "")
        abrir.target = self
        menu.addItem(abrir)
        menu.addItem(.separator())
        let detener = NSMenuItem(title: "Detener Luci", action: #selector(detener), keyEquivalent: "")
        detener.target = self
        menu.addItem(detener)
        item.menu = menu
        self.item = item
        actualizar("cargando")
    }

    private func actualizar(_ nuevo: String) {
        guard nuevo != estado else { return }
        estado = nuevo
        print("estado: \(nuevo)")
        fflush(stdout)
        guard let item = item else { return }
        let (simbolo, texto): (String, String) = {
            switch nuevo {
            case "espera": return ("ladybug", "En espera — llámala: «Luci, …»")
            case "conversando": return ("ladybug.fill", "Te escucha")
            case "pensando": return ("ladybug.fill", "Pensando…")
            case "hablando": return ("ladybug.fill", "Hablando")
            case "silenciado": return ("mic.slash", "Micrófono silenciado")
            case "cargando": return ("ladybug", "Cargando…")
            case "error": return ("exclamationmark.triangle", "Error (revisa «luci registro»)")
            default: return ("ladybug", "Servicio detenido")
            }
        }()
        let imagen = NSImage(systemSymbolName: simbolo, accessibilityDescription: "Luci")
        imagen?.isTemplate = true
        item.button?.image = imagen
        item.button?.appearsDisabled = ["apagada", "cargando"].contains(nuevo)
        lineaEstado.title = "Luci: \(texto)"
        opcionSilenciar.title = nuevo == "silenciado" ? "Reactivar micrófono" : "Silenciar micrófono"
        let disponible = !["apagada", "cargando", "error"].contains(nuevo)
        for opcion in [opcionSilenciar, opcionConversar, opcionNueva] { opcion.isEnabled = disponible }
    }

    // -------------------------------------------------------------- acciones
    @objc private func silenciar() { conexion.enviar("silenciar") }
    @objc private func conversar() { conexion.enviar("conversar") }
    @objc private func nueva() { conexion.enviar("nueva") }

    @objc private func abrirInterfaz() {
        let luci = NSHomeDirectory() + "/.local/bin/luci"
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        if FileManager.default.fileExists(atPath: "/Applications/Ghostty.app") {
            p.arguments = ["-na", "Ghostty", "--args", "-e", luci]
        } else {
            p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
            p.arguments = ["-e", "tell application \"Terminal\" to do script \"\(luci)\"",
                           "-e", "tell application \"Terminal\" to activate"]
        }
        try? p.run()
    }

    @objc private func detener() {
        // Salida normal (código 0): launchd no la reinicia hasta el próximo inicio de sesión.
        NSApp.terminate(nil)
    }
}

let app = NSApplication.shared
let delegado = AppDelegate()
app.delegate = delegado
app.setActivationPolicy(.accessory)   // sin ícono en el Dock
app.run()
