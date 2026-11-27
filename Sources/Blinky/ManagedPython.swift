import Darwin
import Foundation

struct ManagedPython: Decodable, Sendable {
    let pythonPath: String
    let certificatesPath: String

    static func prepare(using path: String, in bridgeDirectory: URL) async throws -> Self {
        try await Task.detached(priority: .userInitiated) {
            let files = FileManager.default
            let script = bridgeDirectory.appendingPathComponent("prepare_runtime.py")
            guard files.fileExists(atPath: script.path),
                  let support = files.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
                throw SetupError.preparationFailed
            }
            let root = support.appendingPathComponent("Blinky/Python", isDirectory: true)
            let candidates = path.isEmpty ? installedInterpreters(files: files) : [path]
            for candidate in candidates where files.isExecutableFile(atPath: candidate) {
                if let result = try run(candidate, script: script, root: root) {
                    return result
                }
            }
            throw SetupError.pythonRequired
        }.value
    }

    private static func installedInterpreters(files: FileManager) -> [String] {
        let frameworks = "/Library/Frameworks/Python.framework/Versions"
        let versions = (try? files.contentsOfDirectory(atPath: frameworks)) ?? []
        var candidates = versions.filter { $0.first?.isNumber == true }
            .sorted { $0.localizedStandardCompare($1) == .orderedDescending }
            .map { "\(frameworks)/\($0)/bin/python3" }
        for prefix in ["/opt/homebrew", "/usr/local"] {
            candidates.append("\(prefix)/bin/python3")
            let formulae = (try? files.contentsOfDirectory(atPath: "\(prefix)/opt")) ?? []
            candidates += formulae.filter { $0.hasPrefix("python@3.") }
                .sorted { $0.localizedStandardCompare($1) == .orderedDescending }
                .map { "\(prefix)/opt/\($0)/libexec/bin/python3" }
        }
        candidates.append("/opt/local/bin/python3")
        // Apple's /usr/bin/python3 can prompt for developer tools on a clean Mac.
        return candidates
    }

    private static func run(_ candidate: String, script: URL, root: URL) throws -> Self? {
        let task = Process()
        let output = Pipe()
        let finished = DispatchSemaphore(value: 0)
        task.executableURL = URL(fileURLWithPath: candidate)
        task.arguments = ["-I", "-B", script.path, root.path]
        var environment = ProcessInfo.processInfo.environment
        environment.removeValue(forKey: "__PYVENV_LAUNCHER__")
        environment.removeValue(forKey: "PYTHONEXECUTABLE")
        task.environment = environment
        task.standardInput = FileHandle.nullDevice
        task.standardOutput = output
        task.standardError = FileHandle.nullDevice
        task.terminationHandler = { _ in finished.signal() }
        do { try task.run() } catch { return nil }
        try? output.fileHandleForWriting.close()
        if finished.wait(timeout: .now() + 180) == .timedOut {
            task.terminate()
            if finished.wait(timeout: .now() + 1) == .timedOut {
                Darwin.kill(task.processIdentifier, SIGKILL)
            }
            throw SetupError.preparationFailed
        }
        if task.terminationStatus == 2 { return nil }
        let data = output.fileHandleForReading.readDataToEndOfFile()
        guard task.terminationStatus == 0,
              let result = try? JSONDecoder().decode(Self.self, from: data),
              FileManager.default.isExecutableFile(atPath: result.pythonPath),
              FileManager.default.isReadableFile(atPath: result.certificatesPath) else {
            throw SetupError.preparationFailed
        }
        return result
    }

    enum SetupError: LocalizedError {
        case pythonRequired
        case preparationFailed

        var errorDescription: String? {
            switch self {
            case .pythonRequired:
                return "Instala Python 3.10 o posterior desde python.org o selecciona tu intérprete en Ajustes."
            case .preparationFailed:
                return "No se ha podido preparar el entorno de Python. Comprueba tu conexión a internet y vuelve a intentarlo."
            }
        }
    }
}
