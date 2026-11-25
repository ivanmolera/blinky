import SwiftUI

struct OnboardingView: View {
    @ObservedObject var store: AppStore
    @FocusState private var focusedField: Field?

    private enum Field { case username, password, code }

    var body: some View {
        ScrollView {
            VStack(spacing: 28) {
                VStack(spacing: 16) {
                    Image(systemName: "house.and.flag.fill")
                        .font(.system(size: 44, weight: .light))
                        .foregroundStyle(.indigo)
                        .frame(width: 102, height: 102)
                        .background(.indigo.opacity(0.09), in: RoundedRectangle(cornerRadius: 30))
                    VStack(spacing: 8) {
                        Text(store.text("Tu casa, desde tu Mac."))
                            .font(.system(size: 32, weight: .bold, design: .rounded))
                        Text(store.text("Consulta tus cámaras Blink, revisa los últimos clips\ny controla tus sistemas desde un mismo lugar."))
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                            .lineSpacing(4)
                    }
                }

                VStack(alignment: .leading, spacing: 18) {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(store.requiresTwoFactor ? store.text("Verifica tu cuenta") : store.text("Conecta con Blink"))
                            .font(.title3.weight(.semibold))
                        Text(store.requiresTwoFactor
                             ? store.text("Introduce el código de verificación que te ha enviado Blink.")
                             : store.text("Utiliza los datos de tu cuenta Blink."))
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }

                    if store.requiresTwoFactor {
                        TextField(store.text("Código de verificación"), text: $store.verificationCode)
                            .textContentType(.oneTimeCode)
                            .focused($focusedField, equals: .code)
                            .onSubmit { verify() }
                        Button { verify() } label: {
                            HStack {
                                Spacer()
                                Text(store.text("Verificar y conectar"))
                                Spacer()
                            }
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(store.isBusy || store.verificationCode.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        Button(store.text("Volver a iniciar sesión")) {
                            Task { await store.disconnect() }
                        }
                        .buttonStyle(.link)
                        .disabled(store.isBusy)
                    } else {
                        VStack(alignment: .leading, spacing: 12) {
                            TextField(store.text("Correo electrónico"), text: $store.username)
                                .textContentType(.username)
                                .focused($focusedField, equals: .username)
                                .onSubmit { focusedField = .password }
                            SecureField(store.text("Contraseña"), text: $store.password)
                                .textContentType(.password)
                                .focused($focusedField, equals: .password)
                                .onSubmit { connect() }
                        }
                        Button { connect() } label: {
                            HStack(spacing: 8) {
                                Image(systemName: "lock.fill")
                                Text(store.text("Conectar cuenta"))
                            }
                            .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(store.isBusy || store.username.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || store.password.isEmpty)
                    }

                    Label(store.text("La sesión se guarda en el llavero de este Mac."), systemImage: "key.horizontal")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .textFieldStyle(.roundedBorder)
                .controlSize(.large)
                .padding(26)
                .frame(maxWidth: 410)
                .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 20))
                .overlay(RoundedRectangle(cornerRadius: 20).strokeBorder(.quaternary))

                if !store.requiresTwoFactor {
                    HStack(spacing: 5) {
                        Text(store.text("¿Quieres echar un vistazo?"))
                            .foregroundStyle(.secondary)
                        Button(store.text("Explorar la demo")) {
                            store.startDemo()
                        }
                        .buttonStyle(.link)
                        .disabled(store.isBusy)
                    }
                    .font(.subheadline)
                }
                Text(store.text("Primera conexión: configura el entorno de Blink en Ajustes."))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 24)
            .padding(.vertical, 42)
            .frame(maxWidth: .infinity)
        }
    }

    private func connect() {
        guard !store.isBusy, !store.username.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !store.password.isEmpty else { return }
        Task { await store.connect() }
    }

    private func verify() {
        guard !store.isBusy, !store.verificationCode.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        Task { await store.verify() }
    }
}
