import SwiftUI
import BlinkyCore

struct ClipsView: View {
    @ObservedObject var store: AppStore
    @State private var search = ""

    private var filteredClips: [BlinkClip] {
        store.snapshot.clips
            .filter { search.isEmpty || $0.cameraName.localizedCaseInsensitiveContains(search) }
            .sorted { $0.createdAt > $1.createdAt }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(store.text("Grabaciones"))
                        .font(.system(size: 30, weight: .bold, design: .rounded))
                    Text(store.text("Los últimos momentos capturados por tus cámaras."))
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if store.isDemo {
                    InfoPill(title: store.text("Demostración"), symbol: "sparkles", color: .indigo)
                }
            }

            if store.isAuthenticated || store.isDemo {
                if let warnings = store.snapshot.recordingWarnings, !warnings.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(Array(warnings.enumerated()), id: \.offset) { _, warning in
                            Label(store.text(warning), systemImage: "exclamationmark.triangle")
                                .font(.caption)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .foregroundStyle(.orange)
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.orange.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
                }
                if store.snapshot.recordingsTruncated == true {
                    Text(store.text("La lista de grabaciones se ha limitado. Actualiza para volver a consultar."))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                HStack {
                    Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                    TextField(store.text("Buscar por cámara"), text: $search)
                        .textFieldStyle(.plain)
                    if !search.isEmpty {
                        Button { search = "" } label: { Image(systemName: "xmark.circle.fill") }
                            .buttonStyle(.plain)
                            .foregroundStyle(.secondary)
                            .accessibilityLabel(store.text("Borrar búsqueda"))
                    }
                }
                .padding(11)
                .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 9))
                .frame(maxWidth: 380)

                if filteredClips.isEmpty {
                    ContentUnavailableView(search.isEmpty ? store.text(store.snapshot.recordingsStatus == "unavailable" ? "No se pudieron cargar las grabaciones" : "Sin grabaciones") : store.text("No hay coincidencias"),
                                           systemImage: search.isEmpty ? "play.rectangle" : "magnifyingglass",
                                           description: Text(search.isEmpty
                                                ? store.text("Los clips disponibles aparecerán aquí al actualizar. Su disponibilidad depende del almacenamiento de tu cuenta.")
                                                : store.text("Prueba con el nombre de otra cámara.")))
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    ScrollView {
                        LazyVStack(spacing: 1) {
                            ForEach(filteredClips) { clip in
                                clipRow(clip)
                                if clip.id != filteredClips.last?.id {
                                    Divider().padding(.leading, 76)
                                }
                            }
                        }
                        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 14))
                    }
                    Label(store.isDemo ? store.text("Los clips de la demo no contienen vídeo real.") : store.text("Descarga un clip para reproducirlo en tu Mac."),
                          systemImage: "info.circle")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            } else {
                ContentUnavailableView(store.text("Conecta tus cámaras"), systemImage: "lock.shield",
                                       description: Text(store.text("Inicia sesión en Blink desde Cámaras para consultar tus grabaciones.")))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .padding(30)
    }

    private func clipRow(_ clip: BlinkClip) -> some View {
        HStack(spacing: 16) {
            Image(systemName: "play.rectangle.fill")
                .font(.system(size: 23, weight: .light))
                .foregroundStyle(.indigo)
                .frame(width: 46, height: 46)
                .background(.indigo.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
            VStack(alignment: .leading, spacing: 5) {
                Text(clip.cameraName).font(.subheadline.weight(.semibold))
                if let source = clip.source {
                    Text(store.text(source == "local" ? "USB" : "Nube"))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                Text(clip.createdAt.formatted(.dateTime.day().month(.abbreviated).year().hour().minute().locale(store.language.locale)))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if let duration = clip.durationSeconds {
                Text("\(duration) s")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            Button {
                Task { await store.download(clip) }
            } label: {
                Label(clip.downloaded ? store.text("Volver a descargar") : store.text("Descargar"), systemImage: "arrow.down.to.line")
            }
            .disabled(store.isBusy)
            .accessibilityLabel(String(format: store.text("Descargar clip de %@ del %@"), clip.cameraName, clip.createdAt.formatted(.dateTime.day().month(.abbreviated).year().hour().minute().locale(store.language.locale))))
        }
        .padding(16)
    }
}
