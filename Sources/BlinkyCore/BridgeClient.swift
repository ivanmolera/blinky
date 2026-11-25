import Foundation
import Darwin

/// Requests run serially on a dedicated queue. Credentials travel through stdin only.
/// This process does not open a network listener or write session files.
public final class BridgeClient: @unchecked Sendable {
    private let queue = DispatchQueue(label: "com.ivanmolera.blinky.bridge", qos: .userInitiated)
    private let pythonURL: URL
    private let scriptURL: URL
    private let timeout: TimeInterval
    private var process: Process?
    private var input: FileHandle?
    private var output: FileHandle?
    private var buffer = Data()
    private let maximumResponseBytes = 128 * 1024 * 1024

    public init(pythonURL: URL, scriptURL: URL, timeout: TimeInterval = 120) {
        self.pythonURL = pythonURL
        self.scriptURL = scriptURL
        self.timeout = timeout
    }

    public func call<Result: Decodable & Sendable>(_ method: String, params: [String: JSONValue] = [:], as type: Result.Type = Result.self) async throws -> Result {
        try await withCheckedThrowingContinuation { continuation in
            queue.async { [self] in
                do {
                    try startIfNeeded()
                    let id = UUID().uuidString
                    try input?.write(contentsOf: BridgeWire.request(id: id, method: method, params: params))
                    let line = try readLine()
                    continuation.resume(returning: try BridgeWire.response(line, id: id, as: type))
                } catch {
                    // A remote failure preserves the authenticated process, e.g. an incorrect MFA code.
                    if !(error is BridgeFailure) { stopProcess() }
                    continuation.resume(throwing: error)
                }
            }
        }
    }

    public func stop() async {
        await withCheckedContinuation { continuation in
            queue.async { [self] in
                stopProcess()
                continuation.resume()
            }
        }
    }

    private func startIfNeeded() throws {
        if let process, process.isRunning { return }
        guard FileManager.default.isExecutableFile(atPath: pythonURL.path) else { throw BridgeClientError.runtimeUnavailable }
        guard FileManager.default.fileExists(atPath: scriptURL.path) else { throw BridgeClientError.scriptUnavailable }
        let task = Process()
        let stdin = Pipe()
        let stdout = Pipe()
        task.executableURL = pythonURL
        task.arguments = ["-u", scriptURL.path]
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment.removeValue(forKey: "PYTHONHOME")
        environment.removeValue(forKey: "PYTHONPATH")
        task.environment = environment
        task.standardInput = stdin
        task.standardOutput = stdout
        // Never forward library diagnostics: exceptions can contain session URLs or tokens.
        task.standardError = FileHandle.nullDevice
        try task.run()
        process = task
        input = stdin.fileHandleForWriting
        output = stdout.fileHandleForReading
        buffer.removeAll(keepingCapacity: false)
    }

    private func readLine() throws -> Data {
        guard let output else { throw BridgeClientError.closed }
        let deadline = Date().addingTimeInterval(timeout)
        while true {
            if let end = buffer.firstIndex(of: 0x0a) {
                let line = Data(buffer[..<end])
                buffer.removeSubrange(...end)
                return line
            }
            let remaining = deadline.timeIntervalSinceNow
            guard remaining > 0 else { throw BridgeClientError.timeout }
            var descriptor = pollfd(fd: output.fileDescriptor, events: Int16(POLLIN | POLLHUP), revents: 0)
            let state = poll(&descriptor, 1, Int32(min(remaining * 1000, 1000)))
            if state < 0 {
                if errno == EINTR { continue }
                throw BridgeClientError.closed
            }
            if state == 0 { continue }
            var bytes = [UInt8](repeating: 0, count: 64 * 1024)
            let count = Darwin.read(output.fileDescriptor, &bytes, bytes.count)
            guard count > 0 else { throw BridgeClientError.closed }
            buffer.append(contentsOf: bytes.prefix(count))
            guard buffer.count <= maximumResponseBytes else { throw BridgeClientError.responseTooLarge }
        }
    }

    private func stopProcess() {
        try? input?.close()
        input = nil
        if let process, process.isRunning {
            // EOF lets the bridge close the live stream and remove temporary media.
            let gracefulDeadline = Date().addingTimeInterval(6)
            while process.isRunning && Date() < gracefulDeadline { Thread.sleep(forTimeInterval: 0.02) }
            if process.isRunning {
                process.terminate()
                let terminationDeadline = Date().addingTimeInterval(1)
                while process.isRunning && Date() < terminationDeadline { Thread.sleep(forTimeInterval: 0.02) }
                if process.isRunning { Darwin.kill(process.processIdentifier, SIGKILL) }
            }
        }
        try? output?.close()
        output = nil
        process = nil
        buffer.removeAll(keepingCapacity: false)
    }

    deinit {
        stopProcess()
    }
}
