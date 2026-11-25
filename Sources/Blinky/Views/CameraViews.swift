import SwiftUI
import AppKit
import BlinkyCore

private func cameraStatusTitle(_ status: String) -> String {
    switch status.lowercased() {
    case "online": return "Conectada"
    case "offline": return "Sin conexión"
    case "unknown", "": return "Estado desconocido"
    default: return status
    }
}

private func batteryTitle(_ battery: String) -> String {
    switch battery.lowercased() {
    case "ok", "good": return "Correcta"
    case "low": return "Baja"
    case "unknown": return "Desconocida"
    default: return battery
    }
}

struct CameraImage: View {
    let camera: BlinkCamera
    let isDemo: Bool

    var body: some View {
        GeometryReader { geometry in
            if let encoded = camera.thumbnailBase64,
               let data = Data(base64Encoded: encoded),
               let image = NSImage(data: data) {
                Image(nsImage: image)
                    .resizable()
                    .scaledToFill()
                    .frame(width: geometry.size.width, height: geometry.size.height)
                    .clipped()
                    .accessibilityLabel("Última captura de \(camera.name)")
            } else {
                ZStack {
                    LinearGradient(colors: [.indigo.opacity(0.14), .cyan.opacity(0.08)],
                                   startPoint: .topLeading, endPoint: .bottomTrailing)
                    Circle()
                        .stroke(.indigo.opacity(0.08), lineWidth: 36)
                        .frame(width: 190, height: 190)
                        .offset(x: geometry.size.width * 0.3, y: -35)
                    VStack(spacing: 10) {
                        Image(systemName: isDemo ? "house.fill" : "video.slash")
                            .font(.system(size: 32, weight: .light))
                        Text(isDemo ? "Vista de demostración" : "Sin imagen")
                            .font(.caption.weight(.medium))
                    }
                    .foregroundStyle(.secondary)
                }
                .frame(width: geometry.size.width, height: geometry.size.height)
                .clipped()
            }
        }
    }
}

struct CameraCard: View {
    let camera: BlinkCamera
    let isDemo: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 0) {
                CameraImage(camera: camera, isDemo: isDemo)
                    .aspectRatio(16 / 9, contentMode: .fit)
                    .overlay(alignment: .topTrailing) {
                        Text("CAPTURA")
                            .font(.system(size: 10, weight: .semibold))
                            .tracking(0.8)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 5)
                            .background(.regularMaterial, in: Capsule())
                            .padding(12)
                    }
                    .overlay(alignment: .bottomLeading) {
                        if camera.status.lowercased() == "offline" {
                            Label("Sin conexión", systemImage: "wifi.slash")
                                .font(.caption.weight(.medium))
                                .padding(.horizontal, 9)
                                .padding(.vertical, 5)
                                .background(.regularMaterial, in: Capsule())
                                .padding(12)
                        }
                    }

                VStack(alignment: .leading, spacing: 12) {
                    HStack(alignment: .firstTextBaseline) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(camera.name)
                                .font(.headline)
                                .lineLimit(1)
                            Text(camera.model)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                        }
                        Spacer(minLength: 8)
                        Image(systemName: "chevron.right")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.tertiary)
                    }
                    HStack(spacing: 14) {
                        Label(camera.motionEnabled.map { $0 ? "Detección activa" : "Detección pausada" } ?? "Detección desconocida",
                              systemImage: camera.motionEnabled == nil ? "questionmark.circle" : (camera.motionEnabled == true ? "figure.walk.motion" : "pause.circle"))
                            .lineLimit(1)
                        Spacer(minLength: 0)
                        if let battery = camera.battery {
                            Image(systemName: batterySymbol(battery))
                                .help("Batería: \(batteryTitle(battery))")
                                .accessibilityLabel("Batería: \(batteryTitle(battery))")
                        }
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
                .padding(16)
            }
            .background(Color(nsColor: .controlBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 16))
            .overlay(RoundedRectangle(cornerRadius: 16).strokeBorder(.quaternary))
            .contentShape(RoundedRectangle(cornerRadius: 16))
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Abrir cámara \(camera.name)")
    }

    private func batterySymbol(_ battery: String) -> String {
        switch battery.lowercased() {
        case "low", "baja", "bajo": return "battery.25percent"
        case "ok", "good": return "battery.100percent"
        default: return "questionmark.circle"
        }
    }
}

struct CameraDetailView: View {
    @ObservedObject var store: AppStore
    let cameraID: String
    @Environment(\.dismiss) private var dismiss

    private var camera: BlinkCamera? {
        store.snapshot.cameras.first { $0.id == cameraID }
    }

    var body: some View {
        VStack(spacing: 0) {
            if let camera {
                HStack {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(camera.name).font(.title2.weight(.bold))
                        Text(camera.model).font(.subheadline).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Cerrar") { dismiss() }
                        .keyboardShortcut(.cancelAction)
                }
                .padding(24)

                CameraImage(camera: camera, isDemo: store.isDemo)
                    .aspectRatio(16 / 9, contentMode: .fit)
                    .frame(maxHeight: 340)
                    .background(.black.opacity(0.03))

                VStack(alignment: .leading, spacing: 20) {
                    HStack(spacing: 16) {
                        detailMetric("Estado", value: cameraStatusTitle(camera.status), symbol: "video")
                        if let battery = camera.battery {
                            detailMetric("Batería", value: batteryTitle(battery), symbol: "battery.100percent")
                        }
                        if let wifi = camera.wifiStrength {
                            detailMetric("Wi-Fi", value: String(wifi), symbol: "wifi")
                        }
                        if let temperature = camera.temperatureCelsius {
                            detailMetric("Temperatura", value: String(format: "%.1f °C", temperature), symbol: "thermometer.medium")
                        }
                        Spacer(minLength: 0)
                    }

                    Toggle(isOn: Binding(
                        get: { camera.motionEnabled ?? false },
                        set: { enabled in Task { await store.setMotionEnabled(camera, enabled: enabled) } }
                    )) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text("Detección de movimiento").font(.subheadline.weight(.medium))
                            Text("La grabación por movimiento también requiere que el sistema esté activado.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .toggleStyle(.switch)
                    .disabled(store.isBusy)

                    HStack(spacing: 12) {
                        Button {
                            Task { await store.refreshThumbnail(camera) }
                        } label: {
                            Label("Nueva captura", systemImage: "camera")
                        }
                        Button {
                            Task { await store.record(camera) }
                        } label: {
                            Label("Grabar clip", systemImage: "record.circle")
                        }
                        Spacer()
                        Button {} label: {
                            Label("Directo", systemImage: "video")
                        }
                        .disabled(true)
                        .help("El vídeo en directo está pendiente de integración y validación con cámaras reales.")
                    }
                    .controlSize(.large)
                    .disabled(store.isBusy)

                    if let error = store.errorMessage {
                        Label(error, systemImage: "exclamationmark.triangle")
                            .font(.caption)
                            .foregroundStyle(.orange)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    HStack {
                        Text(store.isDemo ? "Las acciones de la demo son simuladas." : "El directo aún no está disponible en Blinky.")
                        Spacer()
                        if store.isBusy { ProgressView().controlSize(.small) }
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
                .padding(24)
            } else {
                ContentUnavailableView("Cámara no disponible", systemImage: "video.slash")
                    .padding(40)
                Button("Cerrar") { dismiss() }.padding(.bottom, 20)
            }
        }
        .frame(width: 660)
    }

    private func detailMetric(_ title: String, value: String, symbol: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: symbol).foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.caption).foregroundStyle(.secondary)
                Text(value).font(.subheadline.weight(.medium))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
