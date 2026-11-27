import AppKit
import BlinkyCore
import Combine
import Foundation
import UniformTypeIdentifiers

enum SidebarSection: String, CaseIterable, Identifiable {
    case cameras, clips, settings
    var id: String { rawValue }
}

struct CameraFeedback {
    let message: String
    let isError: Bool
}

@MainActor
final class AppStore: ObservableObject {
    @Published var snapshot = BlinkySnapshot()
    @Published var selectedSection: SidebarSection = .cameras
    @Published var selectedCameraID: String? {
        didSet {
            if oldValue != selectedCameraID { cameraFeedback = nil }
        }
    }
    @Published var username = ""
    @Published var password = ""
    @Published var verificationCode = ""
    @Published var pythonPath: String
    @Published private(set) var isPreparingPython = false
    @Published var isBusy = false
    @Published var isDemo = false
    @Published var isAuthenticated = false
    @Published var requiresTwoFactor = false
    @Published var errorMessage: String?
    @Published var statusMessage: String?
    @Published var lastUpdated: Date?
    @Published var healthDescription = "Conexión sin comprobar"
    @Published var language: AppLanguage {
        didSet { UserDefaults.standard.set(language.rawValue, forKey: "appLanguage") }
    }
    @Published var liveSession: LiveSession?
    @Published var liveCameraID: String?
    @Published var isStartingLive = false
    @Published private(set) var cameraFeedback: CameraFeedback?
    @Published var ffmpegAvailable: Bool?
    let managesPython: Bool

    private let vault = SessionVault()
    private var client: BridgeClient?
    private var clientPythonPath: String?
    private var bootstrapped = false
    private var exportedClipIDs: Set<String> = []
    private var liveGeneration = UUID()
    private var liveMonitor: Task<Void, Never>?

    init() {
        let managed = Bundle.main.object(forInfoDictionaryKey: "BlinkyManagesPython") as? Bool == true
        managesPython = managed
        language = UserDefaults.standard.string(forKey: "appLanguage").flatMap(AppLanguage.init(rawValue:)) ?? .preferred()
        pythonPath = ProcessInfo.processInfo.environment["BLINKY_PYTHON"]
            ?? UserDefaults.standard.string(forKey: managed ? "managedPythonPath" : "pythonPath")
            ?? (managed ? "" : (Bundle.main.object(forInfoDictionaryKey: "BlinkyPythonPath") as? String ?? Self.discoverPython()))
    }

    func text(_ key: String) -> String { AppStrings.text(key, language: language) }

    func bootstrap() async {
        guard !bootstrapped else { return }
        bootstrapped = true
        if CommandLine.arguments.contains("--demo") { startDemo(); return }
        do {
            guard let session = try vault.load() else { return }
            await perform {
                let bridge = try await self.connection()
                let result: AuthenticationResult = try await bridge.call("restore", params: ["session": .string(session)])
                try await self.accept(result, using: bridge)
            }
        } catch { errorMessage = error.localizedDescription }
    }

    func startDemo() {
        guard !isBusy else { return }
        let previous = client
        resetLiveState()
        client = nil
        clientPythonPath = nil
        Task { await previous?.stop() }
        isDemo = true
        exportedClipIDs.removeAll()
        isAuthenticated = false
        requiresTwoFactor = false
        password = ""
        verificationCode = ""
        snapshot = .demo()
        selectedCameraID = nil
        cameraFeedback = nil
        lastUpdated = snapshot.refreshedAt
        selectedSection = .cameras
        errorMessage = nil
        statusMessage = "Modo demo: los datos y las acciones son simulados."
    }

    func connect() async {
        guard !username.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, !password.isEmpty else {
            errorMessage = "Introduce el correo y la contraseña de tu cuenta Blink."
            return
        }
        await perform {
            self.isDemo = false
            let bridge = try await self.connection()
            // The password is not placed in process arguments, preferences, logs, or files.
            let parameters: [String: JSONValue] = ["username": .string(self.username.trimmingCharacters(in: .whitespacesAndNewlines)), "password": .string(self.password)]
            self.password = ""
            let result: AuthenticationResult = try await bridge.call("login", params: parameters)
            try await self.accept(result, using: bridge)
        }
    }

    func verify() async {
        guard !verificationCode.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        await perform {
            let bridge = try await self.connection()
            let code = self.verificationCode.trimmingCharacters(in: .whitespacesAndNewlines)
            self.verificationCode = ""
            let result: AuthenticationResult = try await bridge.call("verify", params: ["code": .string(code)])
            try await self.accept(result, using: bridge)
        }
    }

    func disconnect() async {
        guard !isBusy else { return }
        isBusy = true
        defer { isBusy = false }
        errorMessage = nil
        await stopLive()
        if !isDemo {
            do { try vault.delete() } catch { errorMessage = error.localizedDescription }
        }
        if let client { await client.stop() }
        client = nil
        clientPythonPath = nil
        snapshot = .init()
        selectedCameraID = nil
        cameraFeedback = nil
        exportedClipIDs.removeAll()
        lastUpdated = nil
        isDemo = false
        isAuthenticated = false
        requiresTwoFactor = false
        password = ""
        verificationCode = ""
        statusMessage = "Sesión cerrada en este Mac."
        selectedSection = .cameras
    }

    func refresh() async {
        if isDemo {
            snapshot.refreshedAt = Date()
            lastUpdated = snapshot.refreshedAt
            statusMessage = "Demostración actualizada. No se ha contactado con Blink."
            return
        }
        guard isAuthenticated else { return }
        await perform {
            try await self.loadSnapshot(using: self.connection(), refreshRecordings: true)
        }
    }

    func refreshThumbnail(_ camera: BlinkCamera) async {
        if isDemo { reportCameraFeedback("Demostración: la solicitud de imagen es simulada."); return }
        await action("refresh_thumbnail", parameters: ["camera_id": .string(camera.id)], message: "Imagen solicitada. Blink puede tardar unos segundos en actualizarla.")
    }

    func setSystemArmed(_ system: BlinkSystem, armed: Bool) async {
        if isDemo {
            if let index = snapshot.systems.firstIndex(where: { $0.id == system.id }) { snapshot.systems[index].armed = armed }
            reportCameraFeedback("Demostración: cambio de sistema simulado.")
            return
        }
        await action("set_system_armed", parameters: ["system_id": .string(system.id), "armed": .bool(armed)], message: armed ? "Sistema activado." : "Sistema desactivado.")
    }

    func setMotionEnabled(_ camera: BlinkCamera, enabled: Bool) async {
        guard !isBusy else { return }
        if isDemo {
            if let index = snapshot.cameras.firstIndex(where: { $0.id == camera.id }) { snapshot.cameras[index].motionEnabled = enabled }
            reportCameraFeedback("Demostración: detección de movimiento simulada.")
            return
        }
        await action("set_motion_enabled", parameters: ["camera_id": .string(camera.id), "enabled": .bool(enabled)], message: "Detección de movimiento actualizada.")
        if enabled, errorMessage == nil,
           snapshot.systems.first(where: { $0.id == camera.systemID })?.armed != true {
            reportCameraFeedback("Detección configurada. Activa también el sistema para recibir eventos.")
        }
    }

    func record(_ camera: BlinkCamera) async {
        if isDemo { reportCameraFeedback("Demostración: no se ha grabado ningún vídeo."); return }
        await action("record", parameters: ["camera_id": .string(camera.id)], message: "Grabación solicitada. Actualiza los clips cuando Blink termine de procesarla.")
    }

    func download(_ clip: BlinkClip) async {
        if isDemo { statusMessage = "Los clips de demostración no contienen vídeo descargable."; return }
        guard isAuthenticated, !isBusy else { return }
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.mpeg4Movie]
        panel.nameFieldStringValue = SafeFilename.clip("\(clip.cameraName)-\(clip.id).mp4")
        panel.canCreateDirectories = true
        guard await panel.begin() == .OK, let url = panel.url else { return }
        guard isAuthenticated, !isDemo else { return }
        await perform {
            let bridge = try await self.connection()
            let result: ClipDownload = try await bridge.call("download_clip", params: ["clip_id": .string(clip.id)])
            try await self.persistSession(using: bridge)
            guard result.mimeType == "video/mp4", let bytes = Data(base64Encoded: result.dataBase64), !bytes.isEmpty else {
                throw BridgeClientError.invalidResponse
            }
            try bytes.write(to: url, options: [.atomic])
            self.exportedClipIDs.insert(clip.id)
            if let index = self.snapshot.clips.firstIndex(where: { $0.id == clip.id }) { self.snapshot.clips[index].downloaded = true }
            self.statusMessage = "Clip guardado en \(url.lastPathComponent)."
        }
    }

    func checkRuntime() async {
        await perform {
            let bridge = try await self.connection()
            let health: BridgeHealth = try await bridge.call("health")
            self.ffmpegAvailable = health.ffmpegAvailable
            self.healthDescription = health.blinkpyAvailable
                ? "Python \(health.pythonVersion) · blinkpy \(health.blinkpyVersion ?? "disponible")"
                : (managesPython ? ManagedPython.SetupError.preparationFailed.localizedDescription
                   : "Python disponible; falta blinkpy. Ejecuta scripts/bootstrap.sh.")
        }
    }

    func startLive(_ camera: BlinkCamera) async {
        if isDemo {
            reportCameraFeedback("La demostración no conecta con cámaras reales.", isError: true)
            return
        }
        guard isAuthenticated, !isBusy, !isStartingLive else { return }
        await stopLive()
        let generation = UUID()
        liveGeneration = generation
        liveCameraID = camera.id
        cameraFeedback = nil
        isStartingLive = true
        await perform(reportToCamera: true) {
            let bridge = try await self.connection()
            let session: LiveSession = try await bridge.call("live_start", params: ["camera_id": .string(camera.id)])
            guard self.liveGeneration == generation else {
                let _: EmptyResult = try await bridge.call("live_stop")
                return
            }
            guard session.playbackURL != nil else {
                let _: EmptyResult = try await bridge.call("live_stop")
                throw PlaybackError.invalidURL
            }
            self.liveSession = session
            self.isStartingLive = false
            self.monitorLive(using: bridge, generation: generation)
            try await self.persistSession(using: bridge)
        }
        if liveGeneration == generation {
            isStartingLive = false
        }
    }

    func stopLive() async {
        let hadSession = liveSession != nil || isStartingLive
        resetLiveState()
        if hadSession, let client {
            // Stop remains available while a stream is opening; the transport serializes it.
            let _: EmptyResult? = try? await client.call("live_stop")
        }
    }

    func livePlaybackFailed(sessionID: String) async {
        guard liveSession?.sessionID == sessionID else { return }
        reportCameraFeedback("No se ha podido reproducir el vídeo en directo.", isError: true)
        await stopLive()
    }

    private func resetLiveState() {
        liveGeneration = UUID()
        liveMonitor?.cancel()
        liveMonitor = nil
        liveSession = nil
        liveCameraID = nil
        isStartingLive = false
    }

    private func monitorLive(using bridge: BridgeClient, generation: UUID) {
        liveMonitor?.cancel()
        liveMonitor = Task { [weak self] in
            while !Task.isCancelled {
                do {
                    try await Task.sleep(for: .seconds(2))
                    guard let self, self.liveGeneration == generation, self.liveSession != nil else { return }
                    let status: LiveStatus = try await bridge.call("live_status")
                    guard self.liveGeneration == generation else { return }
                    if !status.active {
                        self.liveSession = nil
                        self.isStartingLive = false
                        if ["failed", "encoder_stopped"].contains(status.reason ?? "") {
                            self.reportCameraFeedback("No se ha podido reproducir el vídeo en directo.", isError: true)
                        } else {
                            self.reportCameraFeedback("La sesión en directo ha terminado.")
                        }
                        return
                    }
                } catch {
                    guard !Task.isCancelled, let self, self.liveGeneration == generation else { return }
                    self.reportCameraFeedback("No se ha podido reproducir el vídeo en directo.", isError: true)
                    await self.stopLive()
                    return
                }
            }
        }
    }

    private func action(_ method: String, parameters: [String: JSONValue], message: String) async {
        guard isAuthenticated else { return }
        await perform(reportToCamera: true) {
            let bridge = try await self.connection()
            let result: CameraActionResult = try await bridge.call(method, params: parameters)
            try await self.persistSession(using: bridge)
            try await self.loadSnapshot(using: bridge)
            self.reportCameraFeedback(result.message ?? message, isError: !result.completed)
        }
    }

    private func accept(_ result: AuthenticationResult, using bridge: BridgeClient) async throws {
        requiresTwoFactor = result.status == "requires_2fa"
        isAuthenticated = result.status == "authenticated"
        statusMessage = result.message
        if let session = result.session { try vault.save(session) }
        if isAuthenticated {
            selectedSection = .cameras
            try await loadSnapshot(using: bridge)
        } else if !requiresTwoFactor {
            try vault.delete()
            snapshot = .init()
            lastUpdated = nil
            errorMessage = result.message ?? "La sesión ha caducado. Vuelve a iniciar sesión."
        }
    }

    private func loadSnapshot(using bridge: BridgeClient, refreshRecordings: Bool = false) async throws {
        let params: [String: JSONValue] = refreshRecordings ? ["refresh_recordings": .bool(true)] : [:]
        var latest: BlinkySnapshot = try await bridge.call("snapshot", params: params)
        for index in latest.clips.indices {
            latest.clips[index].downloaded = exportedClipIDs.contains(latest.clips[index].id)
        }
        snapshot = latest
        lastUpdated = latest.refreshedAt
        try await persistSession(using: bridge)
    }

    private func persistSession(using bridge: BridgeClient) async throws {
        let current: AuthenticationResult = try await bridge.call("session")
        if let session = current.session { try vault.save(session) }
    }

    private func connection() async throws -> BridgeClient {
        let path = NSString(string: pythonPath.trimmingCharacters(in: .whitespacesAndNewlines)).expandingTildeInPath
        guard managesPython || !path.isEmpty else { throw BridgeClientError.runtimeUnavailable }
        if let client, clientPythonPath == path { return client }
        if let client {
            guard !isAuthenticated && !requiresTwoFactor else {
                throw RuntimeConfigurationError()
            }
            await client.stop()
        }
        let bundledScript = Bundle.module.url(forResource: "bridge", withExtension: "py", subdirectory: "Bridge")
            ?? Bundle.module.bundleURL.appendingPathComponent("Bridge/bridge.py")
        let scriptURL = ProcessInfo.processInfo.environment["BLINKY_BRIDGE_PATH"].map { URL(fileURLWithPath: $0) }
            ?? bundledScript
        let runtime: ManagedPython?
        if managesPython {
            isPreparingPython = true
            defer { isPreparingPython = false }
            runtime = try await ManagedPython.prepare(using: path, in: bundledScript.deletingLastPathComponent())
        } else {
            runtime = nil
        }
        let next = BridgeClient(pythonURL: URL(fileURLWithPath: runtime?.pythonPath ?? path),
                                scriptURL: scriptURL, certificatesPath: runtime?.certificatesPath)
        client = next
        clientPythonPath = path
        UserDefaults.standard.set(path, forKey: managesPython ? "managedPythonPath" : "pythonPath")
        return next
    }

    private func reportCameraFeedback(_ message: String, isError: Bool = false) {
        cameraFeedback = CameraFeedback(message: message, isError: isError)
        errorMessage = isError ? message : nil
        statusMessage = isError ? nil : message
    }

    private func perform(reportToCamera: Bool = false, _ operation: @MainActor () async throws -> Void) async {
        guard !isBusy else { return }
        isBusy = true
        errorMessage = nil
        defer { isBusy = false }
        do { try await operation() }
        catch {
            if reportToCamera {
                reportCameraFeedback(error.localizedDescription, isError: true)
            } else {
                errorMessage = error.localizedDescription
            }
            if let failure = error as? BridgeFailure,
               ["authentication_required", "session_expired", "not_authenticated", "auth_required", "verification_required", "invalid_session"].contains(failure.code) {
                isAuthenticated = false
                requiresTwoFactor = false
                snapshot = .init()
                resetLiveState()
            }
            if error is BridgeClientError {
                isAuthenticated = false
                requiresTwoFactor = false
                snapshot = .init()
                resetLiveState()
            }
        }
    }

    private static func discoverPython() -> String {
        let local = URL(fileURLWithPath: FileManager.default.currentDirectoryPath).appendingPathComponent(".venv/bin/python3").path
        return [local, "/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
            .first { FileManager.default.isExecutableFile(atPath: $0) } ?? ""
    }

    private struct RuntimeConfigurationError: LocalizedError {
        var errorDescription: String? { "Cierra la sesión antes de cambiar el intérprete de Python." }
    }

    private enum PlaybackError: LocalizedError {
        case invalidURL
        var errorDescription: String? { "La dirección de reproducción no es válida." }
    }
}
