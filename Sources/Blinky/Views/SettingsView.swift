import SwiftUI

struct SettingsView: View {
    @ObservedObject var store: AppStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Ajustes")
                        .font(.system(size: 30, weight: .bold, design: .rounded))
                    Text("Prepara la conexión de Blink en este Mac.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }

                GroupBox {
                    VStack(alignment: .leading, spacing: 16) {
                        Label("Cuenta Blink", systemImage: "person.crop.circle")
                            .font(.headline)
                        HStack {
                            VStack(alignment: .leading, spacing: 5) {
                                Text(store.isDemo ? "Modo de demostración" : (store.isAuthenticated ? "Cuenta conectada" : "No has conectado una cuenta"))
                                    .font(.subheadline.weight(.medium))
                                Text(store.isDemo ? "Los datos y las acciones son de ejemplo." : "Blinky guarda la sesión en el llavero de macOS.")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            if store.isAuthenticated || store.isDemo {
                                Button(store.isDemo ? "Salir de la demo" : "Desconectar") {
                                    Task { await store.disconnect() }
                                }
                                .disabled(store.isBusy)
                            } else {
                                Button("Conectar cuenta") { store.selectedSection = .cameras }
                            }
                        }
                    }
                    .padding(12)
                }

                GroupBox {
                    VStack(alignment: .leading, spacing: 16) {
                        Label("Entorno de conexión", systemImage: "externaldrive.connected.to.line.below")
                            .font(.headline)
                        Text("Esta primera versión utiliza un entorno local de Python con blinkpy para comunicarse con Blink.")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                        VStack(alignment: .leading, spacing: 7) {
                            Text("Ejecutable de Python")
                                .font(.subheadline.weight(.medium))
                            TextField("Ruta al entorno Python de Blinky", text: $store.pythonPath)
                                .textFieldStyle(.roundedBorder)
                                .font(.system(.body, design: .monospaced))
                                .disabled(store.isAuthenticated || store.requiresTwoFactor || store.isBusy)
                            Text("Ejemplo: /ruta/a/blinky/.venv/bin/python3. Desconecta la cuenta antes de cambiar el entorno.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .textSelection(.enabled)
                        }
                        HStack(spacing: 14) {
                            Button("Comprobar entorno") {
                                Task { await store.checkRuntime() }
                            }
                            .disabled(store.isBusy)
                            Text(store.healthDescription)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .textSelection(.enabled)
                        }
                    }
                    .padding(12)
                }

                GroupBox {
                    VStack(alignment: .leading, spacing: 12) {
                        Label("Acerca de Blinky", systemImage: "info.circle")
                            .font(.headline)
                        Text("Cámaras, capturas y clips en una app nativa para Mac.")
                            .font(.subheadline)
                        Text("Blinky es un proyecto independiente y no está afiliado a Blink ni a Amazon. La conexión requiere internet. El vídeo en directo está pendiente de integración y validación.")
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
    }
}
