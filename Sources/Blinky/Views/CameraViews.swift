import SwiftUI
import AppKit
import BlinkyCore

private func cameraStatusTitle(_ status: String, language: AppLanguage) -> String {
    func text(_ key: String) -> String { AppStrings.text(key, language: language) }
    switch status.lowercased() {
    case "online": return text("Conectada")
    case "offline": return text("Sin conexión")
    case "unknown", "": return text("Estado desconocido")
    default: return status
    }
}

private func batteryTitle(_ battery: String, language: AppLanguage) -> String {
    func text(_ key: String) -> String { AppStrings.text(key, language: language) }
    switch battery.lowercased() {
    case "ok", "good": return text("Correcta")
    case "low": return text("Baja")
    case "unknown": return text("Desconocida")
    default: return battery
    }
}

struct CameraImage: View {
    let camera: BlinkCamera
    let isDemo: Bool
    var language: AppLanguage = .spanish

    private func text(_ key: String) -> String { AppStrings.text(key, language: language) }

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
                    .accessibilityLabel(String(format: text("Última captura de %@"), camera.name))
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
                        Text(isDemo ? text("Vista de demostración") : text("Sin imagen"))
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
    var language: AppLanguage = .spanish
    let action: () -> Void

    private func text(_ key: String) -> String { AppStrings.text(key, language: language) }

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 0) {
                CameraImage(camera: camera, isDemo: isDemo, language: language)
                    .aspectRatio(16 / 9, contentMode: .fit)
                    .overlay(alignment: .topTrailing) {
                        Text(text("CAPTURA"))
                            .font(.system(size: 10, weight: .semibold))
                            .tracking(0.8)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 5)
                            .background(.regularMaterial, in: Capsule())
                            .padding(12)
                    }
                    .overlay(alignment: .bottomLeading) {
                        if camera.status.lowercased() == "offline" {
                            Label(text("Sin conexión"), systemImage: "wifi.slash")
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
                        Label(camera.motionEnabled.map { $0 ? text("Detección activa") : text("Detección pausada") } ?? text("Detección desconocida"),
                              systemImage: camera.motionEnabled == nil ? "questionmark.circle" : (camera.motionEnabled == true ? "figure.walk.motion" : "pause.circle"))
                            .lineLimit(1)
                        Spacer(minLength: 0)
                        if let battery = camera.battery {
                            Image(systemName: batterySymbol(battery))
                                .help(String(format: text("Batería: %@"), batteryTitle(battery, language: language)))
                                .accessibilityLabel(String(format: text("Batería: %@"), batteryTitle(battery, language: language)))
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
        .accessibilityLabel(String(format: text("Abrir cámara %@"), camera.name))
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

    private var sheetHeight: CGFloat {
        let screen = NSApp.keyWindow?.screen ?? NSScreen.main
        let availableHeight = screen?.visibleFrame.height ?? 900
        return min(820, max(320, availableHeight - 140))
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
                    Button(store.text("Cerrar")) { dismiss() }
                        .keyboardShortcut(.cancelAction)
                }
                .padding(24)

                ScrollView {
                    VStack(spacing: 0) {
                        LiveCameraView(store: store, camera: camera)
                            .frame(height: 340)
                            .clipped()

                        VStack(alignment: .leading, spacing: 20) {
                            HStack(spacing: 16) {
                                detailMetric(store.text("Estado"), value: cameraStatusTitle(camera.status, language: store.language), symbol: "video")
                                if let battery = camera.battery {
                                    detailMetric(store.text("Batería"), value: batteryTitle(battery, language: store.language), symbol: "battery.100percent")
                                }
                                if let wifi = camera.wifiStrength {
                                    detailMetric(store.text("Wi-Fi"), value: String(wifi), symbol: "wifi")
                                }
                                if let temperature = camera.temperatureCelsius {
                                    detailMetric(store.text("Temperatura"), value: String(format: "%.1f °C", temperature), symbol: "thermometer.medium")
                                }
                                Spacer(minLength: 0)
                            }

                            Toggle(isOn: Binding(
                                get: { camera.motionEnabled ?? false },
                                set: { enabled in Task { await store.setMotionEnabled(camera, enabled: enabled) } }
                            )) {
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(store.text("Detección de movimiento")).font(.subheadline.weight(.medium))
                                    Text(store.text("La grabación por movimiento también requiere que el sistema esté activado."))
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            }
                            .toggleStyle(.switch)
                            .disabled(store.isBusy)

                            if let system = store.snapshot.systems.first(where: { $0.id == camera.systemID }), system.armed != true {
                                HStack(alignment: .top, spacing: 12) {
                                    Image(systemName: "shield.slash")
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(store.text("Sistema desarmado")).font(.subheadline.weight(.semibold))
                                        Text(store.text("Activa el sistema para que la detección genere eventos y grabaciones."))
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                    }
                                    Spacer()
                                    Button(store.text("Activar sistema")) {
                                        Task { await store.setSystemArmed(system, armed: true) }
                                    }
                                    .disabled(store.isBusy)
                                }
                                .padding(12)
                                .background(Color.orange.opacity(0.09), in: RoundedRectangle(cornerRadius: 10))
                            }

                            HStack(spacing: 12) {
                                Button {
                                    Task { await store.refreshThumbnail(camera) }
                                } label: {
                                    Label(store.text("Nueva captura"), systemImage: "camera")
                                }
                                .disabled(store.isBusy || store.liveSession != nil || store.isStartingLive)
                                Button {
                                    Task { await store.record(camera) }
                                } label: {
                                    Label(store.text("Grabar clip"), systemImage: "record.circle")
                                }
                                .disabled(store.isBusy || store.liveSession != nil || store.isStartingLive)
                                Spacer()
                                Button {
                                    Task {
                                        if store.liveSession != nil || store.isStartingLive { await store.stopLive() }
                                        else { await store.startLive(camera) }
                                    }
                                } label: {
                                    Label(store.text(store.liveSession != nil || store.isStartingLive ? "Detener directo" : "Iniciar directo"), systemImage: "video")
                                }
                                .disabled(store.isBusy && !store.isStartingLive)
                                .help(store.text("El directo utiliza sesiones breves. Puedes iniciarlo de nuevo cuando termine."))
                            }
                            .controlSize(.large)

                            if let feedback = store.cameraFeedback {
                                Label(store.text(feedback.message),
                                      systemImage: feedback.isError ? "exclamationmark.triangle" : "checkmark.circle")
                                    .font(.caption)
                                    .foregroundStyle(feedback.isError ? Color.orange : Color.secondary)
                                    .fixedSize(horizontal: false, vertical: true)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }

                            HStack {
                                Text(store.isDemo ? store.text("Las acciones de la demo son simuladas.") : store.text("Vídeo en directo con audio. Sin conversación bidireccional."))
                                Spacer()
                                if store.isBusy { ProgressView().controlSize(.small) }
                            }
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        }
                        .padding(24)
                    }
                }
            } else {
                ContentUnavailableView(store.text("Cámara no disponible"), systemImage: "video.slash")
                    .padding(40)
                Button(store.text("Cerrar")) { dismiss() }.padding(.bottom, 20)
            }
        }
        .frame(width: 660, height: sheetHeight)
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
