import SwiftUI
import BlinkyCore

struct SettingsView: View {
    @ObservedObject var store: AppStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(store.text("Ajustes"))
                        .font(.system(size: 30, weight: .bold, design: .rounded))
                    Text(store.text("Prepara la conexión de Blink en este Mac."))
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }

                GroupBox {
                    VStack(alignment: .leading, spacing: 12) {
                        Label(store.text("Idioma de la aplicación"), systemImage: "globe")
                            .font(.headline)
                        Picker(store.text("Idioma"), selection: $store.language) {
                            ForEach(AppLanguage.allCases) { language in
                                Text(language.displayName).tag(language)
                            }
                        }
                        .pickerStyle(.menu)
                        .frame(maxWidth: 360)
                        Text(store.text("El idioma se aplica al instante."))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }

                GroupBox {
                    VStack(alignment: .leading, spacing: 16) {
                        Label(store.text("Cuenta Blink"), systemImage: "person.crop.circle")
                            .font(.headline)
                        HStack {
                            VStack(alignment: .leading, spacing: 5) {
                                Text(store.isDemo ? store.text("Modo de demostración") : (store.isAuthenticated ? store.text("Cuenta conectada") : store.text("No has conectado una cuenta")))
                                    .font(.subheadline.weight(.medium))
                                Text(store.isDemo ? store.text("Los datos y las acciones son de ejemplo.") : store.text("Blinky guarda la sesión en el llavero de macOS."))
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            if store.isAuthenticated || store.isDemo {
                                Button(store.isDemo ? store.text("Salir de la demo") : store.text("Desconectar")) {
                                    Task { await store.disconnect() }
                                }
                                .disabled(store.isBusy)
                            } else {
                                Button(store.text("Conectar cuenta")) { store.selectedSection = .cameras }
                            }
                        }
                    }
                    .padding(12)
                }

                GroupBox {
                    VStack(alignment: .leading, spacing: 16) {
                        Label(store.text("Entorno de conexión"), systemImage: "externaldrive.connected.to.line.below")
                            .font(.headline)
                        Text(store.text("Esta primera versión utiliza un entorno local de Python con blinkpy para comunicarse con Blink."))
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                        VStack(alignment: .leading, spacing: 7) {
                            Text(store.text("Ejecutable de Python"))
                                .font(.subheadline.weight(.medium))
                            TextField(store.text("Ruta al entorno Python de Blinky"), text: $store.pythonPath)
                                .textFieldStyle(.roundedBorder)
                                .font(.system(.body, design: .monospaced))
                                .disabled(store.isAuthenticated || store.requiresTwoFactor || store.isBusy)
                            Text(store.text("Ejemplo: /ruta/a/blinky/.venv/bin/python3. Desconecta la cuenta antes de cambiar el entorno."))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .textSelection(.enabled)
                        }
                        HStack(spacing: 14) {
                            Button(store.text("Comprobar entorno")) {
                                Task { await store.checkRuntime() }
                            }
                            .disabled(store.isBusy)
                            Text(store.text(store.healthDescription))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .textSelection(.enabled)
                        }
                        if let available = store.ffmpegAvailable {
                            Label(store.text(available ? "FFmpeg disponible" : "Falta FFmpeg para el vídeo en directo."), systemImage: available ? "checkmark.circle" : "exclamationmark.triangle")
                                .font(.caption)
                                .foregroundStyle(available ? Color.secondary : Color.orange)
                        }
                    }
                    .padding(12)
                }

                GroupBox {
                    VStack(alignment: .leading, spacing: 12) {
                        Label(store.text("Acerca de Blinky"), systemImage: "info.circle")
                            .font(.headline)
                        Text(store.text("Cámaras, capturas y clips en una app nativa para Mac."))
                            .font(.subheadline)
                        Text(store.text("Blinky es un proyecto independiente y no está afiliado a Blink ni a Amazon. La conexión requiere internet."))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .frame(maxWidth: 760, alignment: .leading)
            .padding(30)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .environment(\.locale, store.language.locale)
    }
}
