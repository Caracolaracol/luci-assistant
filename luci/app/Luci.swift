// Luci.app — ícono en la barra de menú, ventana de ajustes y lanzador del servicio.
//
// macOS concede el permiso de micrófono a aplicaciones (.app), no a scripts sueltos. Esta
// app es el "cuerpo" de Luci: arranca el servicio en Python como proceso hijo (que hereda
// el permiso de la app) y muestra el estado en la barra de menú, conectándose al servicio
// por el mismo socket que usa la interfaz de terminal.
//
// Clic en el ícono: ventana de ajustes (estado, modelos, voz). Clic derecho: menú rápido.
//
// La ruta del intérprete de Python está en Contents/Resources/python (la escribe
// `luci configurar`). Variables de entorno para pruebas:
//   LUCI_SIN_SERVICIO=1   no arranca el servicio (solo el ícono, conectado a uno existente)

import Cocoa
import SwiftUI

let rutaSocket = NSHomeDirectory() + "/Library/Application Support/Luci/luci.sock"
let rutaConfig = NSHomeDirectory() + "/.config/luci/config.toml"

final class Conexion {
    private var fd: Int32 = -1
    private let lock = NSLock()
    /// Se llama con cada mensaje del servicio; `["t": "apagada"]` si no hay conexión.
    var alMensaje: (([String: Any]) -> Void)?

    func iniciar() {
        Thread.detachNewThread { [weak self] in self?.bucle() }
    }

    func enviar(_ cmd: String, _ extra: [String: String] = [:]) {
        var obj = extra
        obj["cmd"] = cmd
        guard var datos = try? JSONSerialization.data(withJSONObject: obj) else { return }
        datos.append(0x0A)
        lock.lock(); defer { lock.unlock() }
        guard fd >= 0 else { return }
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
                alMensaje?(["t": "apagada"])
                Thread.sleep(forTimeInterval: 1)
                continue
            }
            lock.lock(); fd = s; lock.unlock()
            enviar("catalogo")
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
                       obj["t"] as? String != "evento", obj["t"] as? String != "historial" {
                        alMensaje?(obj)
                    }
                }
            }
            lock.lock(); close(s); fd = -1; lock.unlock()
            alMensaje?(["t": "apagada"])
            Thread.sleep(forTimeInterval: 0.5)
        }
    }
}

// MARK: - Paleta (luci/app/marca/README.md)

extension Color {
    /// Color que cambia entre modo claro y oscuro.
    static func luci(_ claro: UInt32, _ oscuro: UInt32) -> Color {
        Color(NSColor(name: nil) { apariencia in
            let hex = apariencia.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? oscuro : claro
            return NSColor(srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
                           blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
        })
    }
}

enum Paleta {
    static let laton = Color.luci(0xD6CC6D, 0xD6CC6D)        // principal: el farol
    static let fondo = Color.luci(0xF4EEDC, 0x231914)        // crema / espresso
    static let superficie = Color.luci(0xFFFBF0, 0x3A2A20)   // tarjetas
    static let borde = Color.luci(0xE2D6BC, 0x4E3A2C)
    static let texto = Color.luci(0x231914, 0xF4EEDC)
    static let secundario = Color.luci(0x6B4A35, 0xC4AE94)   // tostado
    static let caramelo = Color.luci(0xA9784F, 0xA9784F)
    static let error = Color.luci(0xA23B27, 0xE8836A)
    static let sobreLaton = Color.luci(0x231914, 0x231914)   // texto sobre latón (10,4:1)
}

// MARK: - Estado compartido con la ventana

struct ModeloLista: Identifiable {
    let id: String
    let nombre: String
    let detalle: String
    let bytes: Int
    let instalado: Bool
    let activo: Bool
    let respaldo: Bool

    init(_ d: [String: Any]) {
        id = d["id"] as? String ?? ""
        nombre = d["nombre"] as? String ?? id
        detalle = d["detalle"] as? String ?? ""
        bytes = d["bytes"] as? Int ?? 0
        instalado = d["instalado"] as? Bool ?? true
        activo = d["activo"] as? Bool ?? false
        respaldo = d["respaldo"] as? Bool ?? false
    }
}

struct HerramientaMCP: Identifiable {
    let nombre: String
    let descripcion: String
    let lectura: Bool
    let permitida: Bool
    var id: String { nombre }

    init(_ d: [String: Any]) {
        nombre = d["nombre"] as? String ?? ""
        descripcion = d["descripcion"] as? String ?? ""
        lectura = d["lectura"] as? Bool ?? false
        permitida = d["permitida"] as? Bool ?? false
    }
}

struct ServidorMCP: Identifiable {
    let id: String
    let tipo: String
    let detalle: String
    let descripcion: String
    let activo: Bool
    let disponible: Bool
    let conectado: Bool
    let error: String
    let herramientas: [HerramientaMCP]

    init(_ d: [String: Any]) {
        id = d["id"] as? String ?? ""
        tipo = d["tipo"] as? String ?? "local"
        detalle = d["detalle"] as? String ?? ""
        descripcion = d["descripcion"] as? String ?? ""
        activo = d["activo"] as? Bool ?? false
        disponible = d["disponible"] as? Bool ?? false
        conectado = d["conectado"] as? Bool ?? false
        error = d["error"] as? String ?? ""
        herramientas = (d["herramientas"] as? [[String: Any]] ?? []).map(HerramientaMCP.init)
    }
}

struct Importable: Identifiable {
    let clave: String
    let nombre: String
    let origen: String
    let tipo: String
    let detalle: String
    var id: String { clave }

    init(_ d: [String: Any]) {
        clave = d["clave"] as? String ?? ""
        nombre = d["nombre"] as? String ?? ""
        origen = d["origen"] as? String ?? ""
        tipo = d["tipo"] as? String ?? "local"
        detalle = d["detalle"] as? String ?? ""
    }
}

struct Descarga {
    let id: String
    let nombre: String
    let progreso: Double
}

final class EstadoLuci: ObservableObject {
    @Published var estado = "apagada"
    @Published var stt: [ModeloLista] = []
    @Published var voz: [ModeloLista] = []
    @Published var llm: [ModeloLista] = []
    @Published var descarga: Descarga?
    @Published var razonamiento = "none"
    @Published var claveMascara = ""
    @Published var servidores: [ServidorMCP] = []
    @Published var importables: [Importable] = []
    var enviar: (String, [String: String]) -> Void = { _, _ in }

    var disponible: Bool { !["apagada", "cargando", "error"].contains(estado) }
    var activa: Bool { ["conversando", "pensando", "hablando"].contains(estado) }

    var textoEstado: String {
        switch estado {
        case "espera": return "En espera — llámala: «Luci, …»"
        case "conversando": return "Te escucha"
        case "pensando": return "Pensando…"
        case "hablando": return "Hablando"
        case "silenciado": return "Micrófono silenciado"
        case "cargando": return "Cargando…"
        case "error": return "Error — revisa «luci registro»"
        default: return "Detenida"
        }
    }
}

func tamano(_ bytes: Int) -> String {
    bytes >= 1_000_000_000 ? String(format: "%.1f GB", Double(bytes) / 1e9).replacingOccurrences(of: ".", with: ",")
                           : "\(bytes / 1_000_000) MB"
}

// MARK: - Ventana de ajustes

struct BotonLuci: ButtonStyle {
    var principal = false
    func makeBody(configuration: Configuration) -> some View {
        Cuerpo(configuration: configuration, principal: principal)
    }

    private struct Cuerpo: View {
        let configuration: ButtonStyleConfiguration
        let principal: Bool
        @Environment(\.isEnabled) private var activo

        var body: some View {
            configuration.label
            .font(.system(size: 12, weight: .medium))
            .padding(.horizontal, 10).padding(.vertical, 5)
            .foregroundStyle(principal ? Paleta.sobreLaton : Paleta.texto)
            .background(RoundedRectangle(cornerRadius: 7)
                .fill(principal ? Paleta.laton : Paleta.superficie))
            .overlay(RoundedRectangle(cornerRadius: 7).stroke(principal ? Color.clear : Paleta.borde))
            .opacity(!activo ? 0.4 : configuration.isPressed ? 0.7 : 1)
        }
    }
}

struct FilaModelo: View {
    let modelo: ModeloLista
    let tipo: String
    @ObservedObject var luci: EstadoLuci
    let elegir: (String, ModeloLista) -> Void
    let borrar: (String, ModeloLista) -> Void

    private var borrable: Bool { modelo.instalado && !modelo.activo && !modelo.id.hasPrefix("say:") }

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: modelo.activo ? "largecircle.fill.circle" : "circle")
                .foregroundStyle(modelo.activo ? Paleta.laton : Paleta.borde)
                .font(.system(size: 14))
            VStack(alignment: .leading, spacing: 2) {
                Text(modelo.nombre).font(.system(size: 13, weight: .medium)).foregroundStyle(Paleta.texto)
                if !modelo.detalle.isEmpty {
                    Text(modelo.detalle).font(.system(size: 11)).foregroundStyle(Paleta.secundario)
                }
            }
            Spacer()
            accion
        }
        .padding(.vertical, 6)
    }

    @ViewBuilder private var accion: some View {
        if let d = luci.descarga, d.id == modelo.id {
            HStack(spacing: 6) {
                // Barra propia: macOS ignora `tint` en la ProgressView lineal.
                Capsule().fill(Paleta.borde).frame(width: 90, height: 6)
                    .overlay(alignment: .leading) {
                        Capsule().fill(Paleta.laton).frame(width: max(6, 90 * d.progreso), height: 6)
                    }
                    .animation(.linear(duration: 0.2), value: d.progreso)
                Text("\(Int(d.progreso * 100)) %").font(.system(size: 11).monospacedDigit())
                    .foregroundStyle(Paleta.secundario).frame(width: 36, alignment: .trailing)
            }
        } else if modelo.activo {
            HStack(spacing: 6) {
                if tipo == "voz" { botonProbar }
                Text("En uso").font(.system(size: 11, weight: .semibold))
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .foregroundStyle(Paleta.sobreLaton)
                    .background(Capsule().fill(Paleta.laton))
            }
        } else if modelo.instalado {
            HStack(spacing: 6) {
                if tipo == "voz" { botonProbar }
                if borrable {
                    Button { borrar(tipo, modelo) } label: { Image(systemName: "trash") }
                        .buttonStyle(BotonLuci())
                        .help("Borrar este modelo del Mac")
                }
                Button("Usar") { elegir(tipo, modelo) }.buttonStyle(BotonLuci())
            }
            .disabled(!luci.disponible)
        } else {
            Button {
                elegir(tipo, modelo)
            } label: {
                Label("Descargar · \(tamano(modelo.bytes))", systemImage: "arrow.down.circle")
            }
            .buttonStyle(BotonLuci())
            .disabled(!luci.disponible || luci.descarga != nil)
        }
    }

    private var botonProbar: some View {
        Button {
            luci.enviar("probar_voz", ["id": modelo.id])
        } label: {
            Image(systemName: "speaker.wave.2")
        }
        .buttonStyle(BotonLuci())
        .help("Escuchar esta voz")
        .disabled(!luci.disponible)
    }
}

struct FilaLenguaje: View {
    let modelo: ModeloLista
    @ObservedObject var luci: EstadoLuci
    let elegir: (String, ModeloLista) -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: modelo.activo ? "largecircle.fill.circle" : "circle")
                .foregroundStyle(modelo.activo ? Paleta.laton : Paleta.borde)
                .font(.system(size: 14))
            Text(modelo.nombre).font(.system(size: 13, weight: .medium)).foregroundStyle(Paleta.texto)
            Spacer()
            if modelo.activo {
                Text("En uso").font(.system(size: 11, weight: .semibold))
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .foregroundStyle(Paleta.sobreLaton)
                    .background(Capsule().fill(Paleta.laton))
            } else {
                Button {
                    luci.enviar("respaldo", ["id": modelo.id, "si": modelo.respaldo ? "" : "1"])
                } label: {
                    Label(modelo.respaldo ? "Respaldo" : "Agregar",
                          systemImage: modelo.respaldo ? "checkmark" : "plus")
                }
                .buttonStyle(BotonLuci())
                .help(modelo.respaldo ? "Quitar de la lista de respaldo" : "Agregar a la lista de respaldo")
                Button("Usar") { elegir("llm", modelo) }.buttonStyle(BotonLuci())
            }
        }
        .padding(.vertical, 5)
    }
}

struct AjustesLenguaje: View {
    @ObservedObject var luci: EstadoLuci
    @State private var clave = ""
    private let niveles = [("none", "Ninguno"), ("minimal", "Mínimo"), ("low", "Bajo"), ("medium", "Medio"),
                           ("high", "Alto")]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Razonamiento").font(.system(size: 13, weight: .medium)).foregroundStyle(Paleta.texto)
                Spacer()
                Picker("", selection: Binding(get: { luci.razonamiento },
                                              set: { luci.enviar("razonamiento", ["valor": $0]) })) {
                    ForEach(niveles, id: \.0) { Text($0.1).tag($0.0) }
                }
                .labelsHidden().frame(width: 130)
            }
            Text("Más razonamiento = respuestas más pensadas, pero tarda más en empezar a hablar. Para voz se recomienda «Ninguno».")
                .font(.system(size: 11)).foregroundStyle(Paleta.secundario)
            Divider().overlay(Paleta.borde)
            HStack(spacing: 8) {
                Text("Clave de OpenCode Go").font(.system(size: 13, weight: .medium)).foregroundStyle(Paleta.texto)
                Spacer()
                Text(luci.claveMascara.isEmpty ? "sin configurar" : luci.claveMascara)
                    .font(.system(size: 11).monospaced())
                    .foregroundStyle(luci.claveMascara.isEmpty ? Paleta.error : Paleta.secundario)
            }
            HStack(spacing: 8) {
                SecureField("Pega una clave nueva", text: $clave)
                    .textFieldStyle(.roundedBorder)
                Button("Guardar") {
                    luci.enviar("clave", ["valor": clave])
                    clave = ""
                }
                .buttonStyle(BotonLuci(principal: true))
                .disabled(clave.trimmingCharacters(in: .whitespaces).count < 10)
            }
        }
        .padding(.vertical, 8)
    }
}

struct FilaServidor: View {
    let s: ServidorMCP
    @ObservedObject var luci: EstadoLuci
    @State private var abierto = false
    @State private var descripcion = ""
    @State private var confirmarQuitar = false

    private var estado: (String, Color) {
        if !s.disponible { return ("No encontré el comando en este Mac", Paleta.error) }
        if !s.activo { return ("Desactivado", Paleta.secundario) }
        if !s.error.isEmpty { return ("No pude conectarme: \(s.error)", Paleta.error) }
        if !s.conectado { return ("Conectando…", Paleta.secundario) }
        let n = s.herramientas.filter { $0.permitida }.count
        return ("\(n) de \(s.herramientas.count) herramientas permitidas", Paleta.secundario)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: s.tipo == "remoto" ? "network" : "terminal")
                    .font(.system(size: 12)).foregroundStyle(Paleta.caramelo).frame(width: 16).padding(.top, 2)
                VStack(alignment: .leading, spacing: 2) {
                    Text(s.id).font(.system(size: 13, weight: .medium)).foregroundStyle(Paleta.texto)
                    Text(s.detalle).font(.system(size: 10).monospaced()).foregroundStyle(Paleta.secundario)
                        .lineLimit(1).truncationMode(.middle)
                    Text(estado.0).font(.system(size: 11)).foregroundStyle(estado.1)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
                Button { confirmarQuitar = true } label: { Image(systemName: "trash") }
                    .buttonStyle(BotonLuci()).help("Quitar este servidor")
                Toggle("", isOn: Binding(get: { s.activo },
                                         set: { luci.enviar("mcp_activar", ["id": s.id, "si": $0 ? "1" : ""]) }))
                    .toggleStyle(.switch).labelsHidden().tint(Paleta.laton).controlSize(.small)
                    .disabled(!s.disponible)
            }
            if s.activo && s.conectado {
                DisclosureGroup(isExpanded: $abierto) {
                    VStack(alignment: .leading, spacing: 8) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Cuándo usarlo").font(.system(size: 11, weight: .medium)).foregroundStyle(Paleta.texto)
                            TextField("Ej.: mis notas de Obsidian; úsalo cuando pregunte por mis notas", text: $descripcion)
                                .textFieldStyle(.roundedBorder).font(.system(size: 12))
                                .onSubmit { luci.enviar("mcp_descripcion", ["id": s.id, "descripcion": descripcion]) }
                            Text("Se lo explica al modelo. Pulsa Intro para guardar.")
                                .font(.system(size: 10)).foregroundStyle(Paleta.secundario)
                        }
                        Divider().overlay(Paleta.borde)
                        ForEach(s.herramientas) { h in
                            Toggle(isOn: Binding(get: { h.permitida },
                                                 set: { luci.enviar("mcp_permitir", ["id": s.id, "herramienta": h.nombre,
                                                                                     "si": $0 ? "1" : ""]) })) {
                                VStack(alignment: .leading, spacing: 1) {
                                    HStack(spacing: 6) {
                                        Text(h.nombre).font(.system(size: 11, weight: .medium).monospaced())
                                            .foregroundStyle(Paleta.texto)
                                        Text(h.lectura ? "lectura" : "modifica")
                                            .font(.system(size: 9, weight: .semibold))
                                            .padding(.horizontal, 5).padding(.vertical, 1)
                                            .background(Capsule().fill(h.lectura ? Paleta.borde : Paleta.caramelo.opacity(0.35)))
                                            .foregroundStyle(h.lectura ? Paleta.secundario : Paleta.texto)
                                    }
                                    if !h.descripcion.isEmpty {
                                        Text(h.descripcion).font(.system(size: 10)).foregroundStyle(Paleta.secundario)
                                            .lineLimit(2)
                                    }
                                }
                            }
                            .toggleStyle(.checkbox).tint(Paleta.laton)
                        }
                    }
                    .padding(.top, 6)
                } label: {
                    Text("Herramientas y descripción").font(.system(size: 11, weight: .medium))
                        .foregroundStyle(Paleta.texto)
                }
                .padding(.leading, 26)
            }
        }
        .padding(.vertical, 8)
        .onAppear { descripcion = s.descripcion }
        .onChange(of: s.descripcion, perform: { descripcion = $0 })
        .alert("¿Quitar «\(s.id)»?", isPresented: $confirmarQuitar) {
            Button("Quitar", role: .destructive) { luci.enviar("mcp_quitar", ["id": s.id]) }
            Button("Cancelar", role: .cancel) {}
        } message: {
            Text("Luci dejará de usar sus herramientas. Puedes volver a agregarlo cuando quieras.")
        }
    }
}

struct VistaAgregarMCP: View {
    @ObservedObject var luci: EstadoLuci
    @Environment(\.dismiss) private var cerrar
    @State private var tipo = "local"
    @State private var nombre = ""
    @State private var comando = ""
    @State private var url = ""
    @State private var variables = ""
    @State private var descripcion = ""

    private var listo: Bool {
        !nombre.trimmingCharacters(in: .whitespaces).isEmpty &&
            !(tipo == "local" ? comando : url).trimmingCharacters(in: .whitespaces).isEmpty
    }

    private func campo(_ titulo: String, _ ayuda: String = "") -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(titulo).font(.system(size: 12, weight: .medium)).foregroundStyle(Paleta.texto)
            if !ayuda.isEmpty {
                Text(ayuda).font(.system(size: 10)).foregroundStyle(Paleta.secundario)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Agregar servidor MCP").font(.system(size: 16, weight: .semibold)).foregroundStyle(Paleta.texto)
            if !luci.importables.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    campo("Ya configurados en otras apps", "Se copian con sus claves; solo quedan permitidas las herramientas de lectura.")
                    ForEach(luci.importables) { i in
                        HStack(spacing: 8) {
                            Image(systemName: i.tipo == "remoto" ? "network" : "terminal")
                                .font(.system(size: 11)).foregroundStyle(Paleta.caramelo).frame(width: 14)
                            VStack(alignment: .leading, spacing: 1) {
                                Text("\(i.nombre) · \(i.origen)").font(.system(size: 12, weight: .medium))
                                    .foregroundStyle(Paleta.texto)
                                Text(i.detalle).font(.system(size: 10).monospaced()).foregroundStyle(Paleta.secundario)
                                    .lineLimit(1).truncationMode(.middle)
                            }
                            Spacer()
                            Button("Agregar") {
                                luci.enviar("mcp_importar", ["clave": i.clave])
                                cerrar()
                            }
                            .buttonStyle(BotonLuci())
                        }
                    }
                }
                .padding(10)
                .background(RoundedRectangle(cornerRadius: 8).fill(Paleta.superficie))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Paleta.borde))
                Text("O a mano:").font(.system(size: 12, weight: .medium)).foregroundStyle(Paleta.secundario)
            }
            Picker("", selection: $tipo) {
                Text("Local (comando)").tag("local")
                Text("Remoto (URL)").tag("remoto")
            }
            .pickerStyle(.segmented).labelsHidden()
            VStack(alignment: .leading, spacing: 4) {
                campo("Nombre")
                TextField("github", text: $nombre).textFieldStyle(.roundedBorder)
            }
            if tipo == "local" {
                VStack(alignment: .leading, spacing: 4) {
                    campo("Comando", "Como aparece en la documentación del servidor.")
                    TextField("npx -y @modelcontextprotocol/server-github", text: $comando)
                        .textFieldStyle(.roundedBorder).font(.system(size: 12).monospaced())
                }
            } else {
                VStack(alignment: .leading, spacing: 4) {
                    campo("URL")
                    TextField("https://ejemplo.com/mcp", text: $url)
                        .textFieldStyle(.roundedBorder).font(.system(size: 12).monospaced())
                }
            }
            VStack(alignment: .leading, spacing: 4) {
                campo(tipo == "local" ? "Variables de entorno (opcional)" : "Cabeceras (opcional)",
                      tipo == "local" ? "Una por línea: GITHUB_TOKEN=…" : "Una por línea: Authorization: Bearer …")
                TextEditor(text: $variables)
                    .font(.system(size: 12).monospaced())
                    .frame(height: 54)
                    .scrollContentBackground(.hidden)
                    .padding(4)
                    .background(RoundedRectangle(cornerRadius: 6).fill(Paleta.superficie))
                    .overlay(RoundedRectangle(cornerRadius: 6).stroke(Paleta.borde))
            }
            VStack(alignment: .leading, spacing: 4) {
                campo("Cuándo usarlo", "Se lo explica al modelo para que sepa cuándo consultarlo.")
                TextField("Mis repositorios de GitHub: úsalo cuando pregunte por issues o PRs", text: $descripcion)
                    .textFieldStyle(.roundedBorder)
            }
            Text("Se guarda en config.toml. Solo quedarán permitidas las herramientas de lectura; las demás puedes activarlas después.")
                .font(.system(size: 10)).foregroundStyle(Paleta.secundario)
            HStack {
                Spacer()
                Button("Cancelar") { cerrar() }.buttonStyle(BotonLuci())
                Button("Agregar") {
                    luci.enviar("mcp_agregar", ["nombre": nombre, "tipo": tipo, "comando": comando, "url": url,
                                                "variables": variables, "descripcion": descripcion])
                    cerrar()
                }
                .buttonStyle(BotonLuci(principal: true)).disabled(!listo)
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 420)
        .background(Paleta.fondo)
    }
}

struct Seccion<Contenido: View>: View {
    let titulo: String
    let icono: String
    @ViewBuilder let contenido: Contenido

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Label(titulo, systemImage: icono)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Paleta.caramelo)
                .textCase(.uppercase)
                .padding(.bottom, 2)
            VStack(spacing: 0) { contenido }
                .padding(.horizontal, 12).padding(.vertical, 4)
                .background(RoundedRectangle(cornerRadius: 10).fill(Paleta.superficie))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(Paleta.borde))
        }
    }
}

struct VistaAjustes: View {
    @ObservedObject var luci: EstadoLuci
    let elegir: (String, ModeloLista) -> Void
    let borrar: (String, ModeloLista) -> Void
    let editarConfig: () -> Void
    @State private var verMas = false
    @State private var agregarMCP = false
    let detener: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            cabecera
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    Seccion(titulo: "Transcripción", icono: "waveform") {
                        lista(luci.stt, tipo: "stt")
                    }
                    Seccion(titulo: "Voz", icono: "speaker.wave.2") {
                        lista(luci.voz, tipo: "voz")
                    }
                    Seccion(titulo: "Modelo de lenguaje", icono: "sparkle") {
                        ForEach(Array(luci.llm.filter { $0.respaldo }.enumerated()), id: \.element.id) { i, m in
                            if i > 0 { Divider().overlay(Paleta.borde) }
                            FilaLenguaje(modelo: m, luci: luci, elegir: elegir)
                        }
                        Text("Si el modelo en uso no responde, Luci prueba con los de respaldo, en este orden.")
                            .font(.system(size: 11)).foregroundStyle(Paleta.secundario)
                            .frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 6)
                        let otros = luci.llm.filter { !$0.respaldo }
                        if !otros.isEmpty {
                            Divider().overlay(Paleta.borde)
                            DisclosureGroup(isExpanded: $verMas) {
                                ForEach(otros) { m in
                                    FilaLenguaje(modelo: m, luci: luci, elegir: elegir)
                                }
                            } label: {
                                Text("Más modelos de OpenCode Go (\(otros.count))")
                                    .font(.system(size: 12, weight: .medium)).foregroundStyle(Paleta.texto)
                            }
                            .padding(.vertical, 6)
                        }
                    }
                    Seccion(titulo: "Ajustes del modelo", icono: "slider.horizontal.3") {
                        AjustesLenguaje(luci: luci)
                    }
                    Seccion(titulo: "Herramientas (MCP)", icono: "books.vertical") {
                        ForEach(Array(luci.servidores.enumerated()), id: \.element.id) { i, srv in
                            if i > 0 { Divider().overlay(Paleta.borde) }
                            FilaServidor(s: srv, luci: luci)
                        }
                        if !luci.servidores.isEmpty { Divider().overlay(Paleta.borde) }
                        HStack {
                            Text("Lo que encuentra se envía al modelo de lenguaje junto con tu pregunta.")
                                .font(.system(size: 11)).foregroundStyle(Paleta.secundario)
                            Spacer()
                            Button { agregarMCP = true } label: { Label("Agregar…", systemImage: "plus") }
                                .buttonStyle(BotonLuci())
                        }
                        .padding(.vertical, 8)
                    }
                    Text("Los modelos se procesan en este Mac. Solo el texto de lo que le dices a Luci se envía al modelo de lenguaje.")
                        .font(.system(size: 11)).foregroundStyle(Paleta.secundario)
                }
                .padding(18)
            }
            pie
        }
        .frame(width: 480, height: 660)
        .background(Paleta.fondo)
        .sheet(isPresented: $agregarMCP) { VistaAgregarMCP(luci: luci) }
    }

    @ViewBuilder private func lista(_ modelos: [ModeloLista], tipo: String) -> some View {
        if modelos.isEmpty {
            Text(luci.estado == "apagada" ? "Luci está detenida." : "Cargando…")
                .font(.system(size: 12)).foregroundStyle(Paleta.secundario).padding(.vertical, 8)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        ForEach(Array(modelos.enumerated()), id: \.element.id) { i, m in
            if i > 0 { Divider().overlay(Paleta.borde) }
            FilaModelo(modelo: m, tipo: tipo, luci: luci, elegir: elegir, borrar: borrar)
        }
    }

    private var cabecera: some View {
        HStack(spacing: 14) {
            Image(nsImage: NSApp.applicationIconImage)
                .resizable().frame(width: 56, height: 56)
                .shadow(color: luci.activa ? Paleta.laton.opacity(0.8) : .clear, radius: 10)
                .animation(.easeInOut(duration: 0.3), value: luci.activa)
            VStack(alignment: .leading, spacing: 4) {
                Text("Luci").font(.system(size: 22, weight: .bold)).foregroundStyle(Paleta.texto)
                HStack(spacing: 6) {
                    Circle().fill(luci.estado == "error" ? Paleta.error
                                  : luci.activa ? Paleta.laton : Paleta.secundario.opacity(0.6))
                        .frame(width: 8, height: 8)
                    Text(luci.textoEstado).font(.system(size: 12)).foregroundStyle(Paleta.secundario)
                }
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 6) {
                Button {
                    luci.enviar("silenciar", [:])
                } label: {
                    Label(luci.estado == "silenciado" ? "Reactivar" : "Silenciar",
                          systemImage: luci.estado == "silenciado" ? "mic" : "mic.slash")
                }
                .buttonStyle(BotonLuci(principal: luci.estado == "silenciado"))
                HStack(spacing: 6) {
                    Button { luci.enviar("conversar", [:]) } label: { Image(systemName: "bubble.left") }
                        .help("Hablarle sin decir su nombre")
                    Button { luci.enviar("nueva", [:]) } label: { Image(systemName: "arrow.counterclockwise") }
                        .help("Nueva conversación (olvida lo hablado)")
                }
                .buttonStyle(BotonLuci())
            }
            .disabled(!luci.disponible)
        }
        .padding(.horizontal, 18).padding(.bottom, 16)
        .padding(.top, 30)            // espacio para los botones de la ventana
        .background(Paleta.superficie)
        .overlay(alignment: .bottom) { Rectangle().fill(Paleta.borde).frame(height: 1) }
    }

    private var pie: some View {
        HStack {
            Button("Editar configuración", action: editarConfig)
            Spacer()
            Button("Detener Luci", action: detener)
        }
        .buttonStyle(BotonLuci())
        .padding(.horizontal, 18).padding(.vertical, 12)
        .background(Paleta.superficie)
        .overlay(alignment: .top) { Rectangle().fill(Paleta.borde).frame(height: 1) }
    }
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private var item: NSStatusItem?
    private var servicio: Process?
    private var saliendo = false
    private var permitirSalir = false     // solo «Detener Luci» o launchd la apagan
    private let conexion = Conexion()
    private let luci = EstadoLuci()
    private var estado = ""
    private var iconoLuz: NSImage?
    private var iconoApagado: NSImage?
    private var ventana: NSWindow?
    private let menuRapido = NSMenu()
    private let lineaEstado = NSMenuItem(title: "Luci: conectando…", action: nil, keyEquivalent: "")
    private let opcionSilenciar = NSMenuItem(title: "Silenciar micrófono", action: #selector(silenciar), keyEquivalent: "")
    private let opcionConversar = NSMenuItem(title: "Hablarle sin decir su nombre", action: #selector(conversar), keyEquivalent: "")
    private let opcionNueva = NSMenuItem(title: "Nueva conversación", action: #selector(nueva), keyEquivalent: "")

    func applicationDidFinishLaunching(_ aviso: Notification) {
        if let ruta = Bundle.main.path(forResource: "Luci", ofType: "icns"), let icono = NSImage(contentsOfFile: ruta) {
            NSApp.applicationIconImage = icono
        }
        if ProcessInfo.processInfo.environment["LUCI_SIN_SERVICIO"] == nil {
            iniciarServicio()
        }
        luci.enviar = { [weak self] cmd, extra in self?.conexion.enviar(cmd, extra) }
        crearMenuPrincipal()
        if iconoActivado() {
            crearIcono()
        }
        conexion.alMensaje = { [weak self] msg in
            DispatchQueue.main.async { self?.recibir(msg) }
        }
        conexion.iniciar()

        // launchd detiene el servicio con SIGTERM: se cierra ordenadamente.
        signal(SIGTERM, SIG_IGN)
        let fuente = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        fuente.setEventHandler { [weak self] in
            self?.permitirSalir = true
            NSApp.terminate(nil)
        }
        fuente.resume()
        senalTerm = fuente
    }
    private var senalTerm: DispatchSourceSignal?

    /// Cmd+Q con la ventana abierta solo la cierra: apagar a Luci es «Detener Luci».
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if permitirSalir { return .terminateNow }
        ventana?.close()
        return .terminateCancel
    }

    /// Clic en el ícono del Dock: muestra la ventana.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        abrirAjustes()
        return true
    }

    // El ícono del Dock aparece solo mientras la ventana está abierta y sin minimizar.
    func windowWillClose(_ aviso: Notification) { NSApp.setActivationPolicy(.accessory) }
    func windowDidMiniaturize(_ aviso: Notification) { NSApp.setActivationPolicy(.accessory) }
    func windowDidDeminiaturize(_ aviso: Notification) { mostrarEnDock() }

    private func mostrarEnDock() {
        NSApp.setActivationPolicy(.regular)
        if let ruta = Bundle.main.path(forResource: "Luci", ofType: "icns"), let icono = NSImage(contentsOfFile: ruta) {
            NSApp.applicationIconImage = icono     // al cambiar de política macOS puede olvidarlo
        }
    }

    private func crearMenuPrincipal() {
        // Menú de la app mientras está en el Dock: cerrar ventana, minimizar, copiar y pegar.
        let principal = NSMenu()
        let app = NSMenuItem()
        app.submenu = NSMenu()
        app.submenu?.addItem(withTitle: "Cerrar ventana", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        app.submenu?.addItem(withTitle: "Minimizar", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        app.submenu?.addItem(.separator())
        let detener = NSMenuItem(title: "Detener Luci", action: #selector(detener), keyEquivalent: "")
        detener.target = self
        app.submenu?.addItem(detener)
        principal.addItem(app)
        NSApp.mainMenu = principal
    }

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

    // ------------------------------------------------------------- mensajes
    private func recibir(_ msg: [String: Any]) {
        switch msg["t"] as? String {
        case "apagada":
            actualizar("apagada")
            if luci.descarga != nil { luci.descarga = nil }
        case "estado":
            if let e = msg["estado"] as? String { actualizar(e) }
            let d = msg["descarga"] as? [String: Any]
            let nueva = d.map { Descarga(id: $0["id"] as? String ?? "", nombre: $0["nombre"] as? String ?? "",
                                         progreso: $0["progreso"] as? Double ?? 0) }
            if nueva?.id != luci.descarga?.id || nueva?.progreso != luci.descarga?.progreso {
                luci.descarga = nueva
            }
        case "catalogo":
            luci.stt = (msg["stt"] as? [[String: Any]] ?? []).map(ModeloLista.init)
            luci.voz = (msg["voz"] as? [[String: Any]] ?? []).map(ModeloLista.init)
            luci.llm = (msg["llm"] as? [[String: Any]] ?? []).map(ModeloLista.init)
            luci.servidores = (msg["herramientas"] as? [[String: Any]] ?? []).map(ServidorMCP.init)
            luci.importables = (msg["importables"] as? [[String: Any]] ?? []).map(Importable.init)
            if let a = msg["llm_ajustes"] as? [String: Any] {
                luci.razonamiento = a["razonamiento"] as? String ?? "none"
                luci.claveMascara = a["clave"] as? String ?? ""
            }
        case "aviso":
            if msg["nivel"] as? String == "error", let texto = msg["texto"] as? String {
                alerta("Luci", texto)
            }
        default:
            break
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

    private func plantilla(_ nombre: String) -> NSImage? {
        guard let ruta = Bundle.main.path(forResource: nombre, ofType: "png"),
              let imagen = NSImage(contentsOfFile: ruta) else { return nil }
        imagen.size = NSSize(width: 18, height: 18)
        imagen.isTemplate = true
        return imagen
    }

    private func crearIcono() {
        iconoApagado = plantilla("menu")
        iconoLuz = plantilla("menu-luz")
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        // Clic: ventana de ajustes. Clic derecho: menú rápido.
        item.button?.target = self
        item.button?.action = #selector(clicIcono)
        item.button?.sendAction(on: [.leftMouseUp, .rightMouseUp])

        menuRapido.autoenablesItems = false
        lineaEstado.isEnabled = false
        menuRapido.addItem(lineaEstado)
        menuRapido.addItem(.separator())
        for opcion in [opcionSilenciar, opcionConversar, opcionNueva] {
            opcion.target = self
            menuRapido.addItem(opcion)
        }
        menuRapido.addItem(.separator())
        for (titulo, accion) in [("Ajustes…", #selector(abrirAjustes)), ("Abrir interfaz", #selector(abrirInterfaz)),
                                 ("Detener Luci", #selector(detener))] {
            let opcion = NSMenuItem(title: titulo, action: accion, keyEquivalent: "")
            opcion.target = self
            menuRapido.addItem(opcion)
        }
        self.item = item
        actualizar("cargando")
    }

    @objc private func clicIcono() {
        let evento = NSApp.currentEvent
        if evento?.type == .rightMouseUp || evento?.modifierFlags.contains(.control) == true {
            item?.menu = menuRapido
            item?.button?.performClick(nil)
            item?.menu = nil          // el próximo clic izquierdo vuelve a abrir la ventana
        } else {
            abrirAjustes()
        }
    }

    @objc private func abrirAjustes() {
        if ventana == nil {
            let vista = VistaAjustes(luci: luci, elegir: { [weak self] tipo, m in self?.elegir(tipo, m) },
                                     borrar: { [weak self] tipo, m in self?.borrar(tipo, m) },
                                     editarConfig: { [weak self] in self?.editarConfig() },
                                     detener: { [weak self] in self?.detener() })
            let v = NSWindow(contentViewController: NSHostingController(rootView: vista))
            v.title = "Luci"
            v.styleMask = [.titled, .closable, .miniaturizable, .fullSizeContentView]
            v.titlebarAppearsTransparent = true
            v.isReleasedWhenClosed = false
            v.delegate = self
            v.center()
            ventana = v
        }
        conexion.enviar("catalogo")
        mostrarEnDock()
        if ventana?.isMiniaturized == true { ventana?.deminiaturize(nil) }
        NSApp.activate(ignoringOtherApps: true)
        ventana?.makeKeyAndOrderFront(nil)
    }

    private func actualizar(_ nuevo: String) {
        guard nuevo != estado else { return }
        estado = nuevo
        luci.estado = nuevo
        print("estado: \(nuevo)")
        fflush(stdout)
        guard let item = item else { return }
        // Farol relleno cuando te escucha, piensa o habla; en contorno en espera.
        let (simbolo, propio): (String, NSImage?) = {
            switch nuevo {
            case "conversando", "pensando", "hablando": return ("ladybug.fill", iconoLuz)
            case "silenciado": return ("mic.slash", nil)
            case "error": return ("exclamationmark.triangle", nil)
            default: return ("ladybug", iconoApagado)
            }
        }()
        let imagen = propio ?? NSImage(systemSymbolName: simbolo, accessibilityDescription: "Luci")
        imagen?.isTemplate = true
        item.button?.image = imagen
        item.button?.appearsDisabled = ["apagada", "cargando"].contains(nuevo)
        lineaEstado.title = "Luci: \(luci.textoEstado)"
        opcionSilenciar.title = nuevo == "silenciado" ? "Reactivar micrófono" : "Silenciar micrófono"
        for opcion in [opcionSilenciar, opcionConversar, opcionNueva] { opcion.isEnabled = luci.disponible }
    }

    // ------------------------------------------------------------- ajustes
    private func elegir(_ tipo: String, _ m: ModeloLista) {
        if !m.instalado {
            let ok = confirmar("¿Descargar \(m.nombre)?",
                               "Se descargarán \(tamano(m.bytes)) desde Hugging Face. Luci lo usará en cuanto termine.")
            guard ok else { return }
        }
        conexion.enviar("usar", ["tipo": tipo, "id": m.id])
    }

    private func borrar(_ tipo: String, _ m: ModeloLista) {
        var texto = "Se liberará el espacio en disco. Podrás volver a descargarlo desde esta ventana."
        if tipo == "stt" {
            texto += " Está en la caché de Hugging Face: si otra aplicación lo usa, también tendrá que descargarlo de nuevo."
        }
        guard confirmar("¿Borrar \(m.nombre)?", texto, boton: "Borrar", peligroso: true) else { return }
        conexion.enviar("borrar", ["tipo": tipo, "id": m.id])
    }

    private func confirmar(_ titulo: String, _ texto: String, boton: String = "Descargar",
                           peligroso: Bool = false) -> Bool {
        NSApp.activate(ignoringOtherApps: true)
        let a = NSAlert()
        a.messageText = titulo
        a.informativeText = texto
        a.addButton(withTitle: boton).hasDestructiveAction = peligroso
        a.addButton(withTitle: "Cancelar")
        return a.runModal() == .alertFirstButtonReturn
    }

    private func alerta(_ titulo: String, _ texto: String) {
        NSApp.activate(ignoringOtherApps: true)
        let a = NSAlert()
        a.messageText = titulo
        a.informativeText = texto
        a.alertStyle = .warning
        a.runModal()
    }

    private func editarConfig() {
        // `open -t`: editor de texto predeterminado (un .toml podría abrirse en Xcode).
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        p.arguments = ["-t", rutaConfig]
        try? p.run()
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
        permitirSalir = true
        NSApp.terminate(nil)
    }
}

let app = NSApplication.shared
let delegado = AppDelegate()
app.delegate = delegado
app.setActivationPolicy(.accessory)   // sin ícono en el Dock
app.run()
