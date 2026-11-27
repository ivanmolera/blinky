import SwiftUI
import BlinkyCore

struct ContentView: View {
    @ObservedObject var store: AppStore
    @State private var selectedCamera: BlinkCamera?

    var body: some View {
        NavigationSplitView {
            sidebar
                .navigationSplitViewColumnWidth(min: 190, ideal: 215, max: 260)
        } detail: {
            VStack(spacing: 0) {
                if let error = store.errorMessage {
                    errorBanner(error)
                }
                Group {
                    switch store.selectedSection {
                    case .cameras:
                        if store.isAuthenticated || store.isDemo {
                            cameraDashboard
                        } else {
                            OnboardingView(store: store)
                        }
                    case .clips:
                        ClipsView(store: store)
                    case .settings:
                        SettingsView(store: store)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                statusBar
            }
            .background(Color(nsColor: .windowBackgroundColor))
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    if store.isAuthenticated || store.isDemo {
                        Button {
                            Task { await store.refresh() }
                        } label: {
                            Label(store.text("Actualizar"), systemImage: "arrow.clockwise")
                        }
                        .keyboardShortcut("r", modifiers: .command)
                        .disabled(store.isBusy)
                        .help(store.text("Actualizar cámaras y grabaciones"))
                    }
                }
            }
        }
        .tint(.indigo)
        .environment(\.locale, store.language.locale)
        .frame(minWidth: 900, minHeight: 620)
        .sheet(item: $selectedCamera) { camera in
            CameraDetailView(store: store, cameraID: camera.id)
        }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                Image(systemName: "video.fill")
                    .font(.system(size: 20, weight: .semibold))
                    .foregroundStyle(.white)
                    .frame(width: 40, height: 40)
                    .background(.indigo.gradient, in: RoundedRectangle(cornerRadius: 12))
                VStack(alignment: .leading, spacing: 1) {
                    Text("Blinky")
                        .font(.title2.weight(.bold))
                    Text(store.text("Tu casa, a mano"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 18)
            .padding(.top, 22)
            .padding(.bottom, 24)

            ScrollView {
                VStack(spacing: 5) {
                    navigationItem(store.text("Cámaras"), symbol: "square.grid.2x2", section: .cameras,
                                   count: store.snapshot.cameras.count)
                    navigationItem(store.text("Grabaciones"), symbol: "play.rectangle", section: .clips,
                                   count: store.snapshot.clips.count)
                    navigationItem(store.text("Ajustes"), symbol: "slider.horizontal.3", section: .settings)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 4)
            }
            .frame(maxHeight: .infinity, alignment: .top)

            VStack(alignment: .leading, spacing: 10) {
                if store.isDemo {
                    Label(store.text("Modo demo"), systemImage: "sparkles")
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(.indigo)
                    Text(store.text("Explora la app con datos de ejemplo."))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    Label(store.isAuthenticated ? store.text("Cuenta conectada") : store.text("Sin conectar"),
                          systemImage: store.isAuthenticated ? "checkmark.shield" : "lock.shield")
                        .font(.subheadline.weight(.medium))
                    Text(store.isAuthenticated ? store.text("Conexión gestionada en este Mac.") : store.text("Conecta tu cuenta para ver tus cámaras."))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if store.isDemo || store.isAuthenticated {
                    Button(store.isDemo ? store.text("Salir de la demo") : store.text("Desconectar cuenta")) {
                        Task { await store.disconnect() }
                    }
                    .font(.caption)
                    .buttonStyle(.link)
                    .disabled(store.isBusy)
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.secondary.opacity(0.07), in: RoundedRectangle(cornerRadius: 12))
            .padding(12)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .safeAreaPadding(.top, 12)
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.45))
    }

    private func navigationItem(_ title: String, symbol: String, section: SidebarSection, count: Int? = nil) -> some View {
        Button {
            store.selectedSection = section
        } label: {
            HStack(spacing: 10) {
                Image(systemName: symbol)
                    .font(.system(size: 15, weight: .medium))
                    .frame(width: 22)
                Text(title)
                    .font(.system(size: 14, weight: .medium))
                Spacer()
                if let count, count > 0 {
                    Text(count.formatted())
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 11)
            .contentShape(Rectangle())
            .background(store.selectedSection == section ? Color.indigo.opacity(0.14) : .clear,
                        in: RoundedRectangle(cornerRadius: 9))
            .foregroundStyle(store.selectedSection == section ? Color.indigo : Color.primary)
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(store.selectedSection == section ? .isSelected : [])
    }

    private var cameraDashboard: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(store.text("Tus cámaras"))
                            .font(.system(size: 30, weight: .bold, design: .rounded))
                        Text(store.text("Una mirada a lo que más importa."))
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    if store.isDemo {
                        InfoPill(title: store.text("Demostración"), symbol: "sparkles", color: .indigo)
                    }
                }

                if !store.snapshot.systems.isEmpty {
                    VStack(alignment: .leading, spacing: 10) {
                        Text(store.text("SISTEMAS"))
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)
                        ForEach(store.snapshot.systems) { system in
                            HStack(spacing: 12) {
                                Image(systemName: system.armed == true ? "shield.lefthalf.filled" : "shield")
                                    .font(.title3)
                                    .foregroundStyle(system.armed == true ? Color.indigo : Color.secondary)
                                    .frame(width: 28)
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(system.name)
                                        .font(.subheadline.weight(.semibold))
                                    Text(system.armed.map { $0 ? store.text("Sistema activado") : store.text("Sistema desactivado") } ?? store.text("Estado desconocido"))
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                Spacer()
                                Toggle(store.text("Activar sistema"), isOn: Binding(
                                    get: { system.armed ?? false },
                                    set: { armed in Task { await store.setSystemArmed(system, armed: armed) } }
                                ))
                                .labelsHidden()
                                .toggleStyle(.switch)
                                .disabled(store.isBusy)
                                .accessibilityLabel(String(format: store.text("Activar sistema %@"), system.name))
                            }
                            .padding(16)
                            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 14))
                        }
                    }
                }

                if store.snapshot.cameras.isEmpty {
                    ContentUnavailableView(store.text("No hay cámaras disponibles"), systemImage: "video.slash",
                                           description: Text(store.text("Actualiza para consultar los dispositivos de tu cuenta Blink.")))
                        .frame(minHeight: 260)
                } else {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 255), spacing: 18)], spacing: 18) {
                        ForEach(store.snapshot.cameras) { camera in
                            CameraCard(camera: camera, isDemo: store.isDemo, language: store.language) {
                                store.selectedCameraID = camera.id
                                selectedCamera = camera
                            }
                        }
                    }
                }

                Label(store.text("El directo está disponible bajo demanda en sesiones breves."), systemImage: "info.circle")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(30)
            .frame(maxWidth: 1200, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
    }

    private func errorBanner(_ message: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange)
            Text(store.text(message))
                .font(.subheadline)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
            Button {
                store.errorMessage = nil
            } label: {
                Image(systemName: "xmark")
            }
            .buttonStyle(.plain)
            .accessibilityLabel(store.text("Cerrar aviso"))
        }
        .padding(14)
        .background(Color.orange.opacity(0.1))
    }

    private var statusBar: some View {
        HStack(spacing: 8) {
            if store.isBusy {
                ProgressView().controlSize(.small)
                Text(store.text(store.isPreparingPython ? "Preparando el entorno de Python…" : "Procesando…"))
            } else if let message = store.statusMessage {
                Text(store.text(message))
                    .lineLimit(1)
            } else {
                Text(store.isDemo ? store.text("Datos de ejemplo · No se ha accedido a Blink") : store.text("Blinky para Mac"))
            }
            Spacer(minLength: 10)
            if let updated = store.lastUpdated {
                Text(String(format: store.text("Actualizado %@"), updated.formatted(.dateTime.hour().minute().locale(store.language.locale))))
                    .monospacedDigit()
            }
        }
        .font(.caption)
        .foregroundStyle(.secondary)
        .padding(.horizontal, 20)
        .padding(.vertical, 10)
        .background(.bar)
    }
}

struct InfoPill: View {
    let title: String
    let symbol: String
    var color: Color = .secondary

    var body: some View {
        Label(title, systemImage: symbol)
            .font(.caption.weight(.medium))
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .foregroundStyle(color)
            .background(color.opacity(0.1), in: Capsule())
    }
}
