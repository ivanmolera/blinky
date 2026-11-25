import Foundation

public struct BlinkySnapshot: Codable, Sendable, Equatable {
    public var systems: [BlinkSystem]
    public var cameras: [BlinkCamera]
    public var clips: [BlinkClip]
    public var refreshedAt: Date?

    public init(systems: [BlinkSystem] = [], cameras: [BlinkCamera] = [], clips: [BlinkClip] = [], refreshedAt: Date? = nil) {
        self.systems = systems
        self.cameras = cameras
        self.clips = clips
        self.refreshedAt = refreshedAt
    }
}

public struct BlinkSystem: Codable, Identifiable, Sendable, Equatable {
    public let id: String
    public var name: String
    public var armed: Bool?

    public init(id: String, name: String, armed: Bool?) {
        self.id = id
        self.name = name
        self.armed = armed
    }
}

public struct BlinkCamera: Codable, Identifiable, Sendable, Equatable {
    public let id: String
    public let systemID: String
    public var name: String
    public var model: String
    public var status: String
    public var battery: String?
    public var wifiStrength: Int?
    public var temperatureCelsius: Double?
    public var motionEnabled: Bool?
    public var thumbnailBase64: String?
    public var updatedAt: Date?

    public init(id: String, systemID: String, name: String, model: String, status: String = "unknown", battery: String? = nil, wifiStrength: Int? = nil, temperatureCelsius: Double? = nil, motionEnabled: Bool? = nil, thumbnailBase64: String? = nil, updatedAt: Date? = nil) {
        self.id = id
        self.systemID = systemID
        self.name = name
        self.model = model
        self.status = status
        self.battery = battery
        self.wifiStrength = wifiStrength
        self.temperatureCelsius = temperatureCelsius
        self.motionEnabled = motionEnabled
        self.thumbnailBase64 = thumbnailBase64
        self.updatedAt = updatedAt
    }
}

public struct BlinkClip: Codable, Identifiable, Sendable, Equatable {
    public let id: String
    public let cameraID: String
    public let cameraName: String
    public let createdAt: Date
    public var durationSeconds: Int?
    public var downloaded: Bool

    public init(id: String, cameraID: String, cameraName: String, createdAt: Date, durationSeconds: Int? = nil, downloaded: Bool = false) {
        self.id = id
        self.cameraID = cameraID
        self.cameraName = cameraName
        self.createdAt = createdAt
        self.durationSeconds = durationSeconds
        self.downloaded = downloaded
    }
}

public struct AuthenticationResult: Codable, Sendable {
    public let status: String
    public let message: String?
    public let session: String?
}

public struct BridgeHealth: Codable, Sendable {
    public let bridgeVersion: String
    public let pythonVersion: String
    public let blinkpyAvailable: Bool
    public let blinkpyVersion: String?
}

public struct ClipDownload: Codable, Sendable {
    public let dataBase64: String
    public let mimeType: String
    public let filename: String
}

public struct EmptyResult: Codable, Sendable {
    public init() {}
}

public extension BlinkySnapshot {
    /// Deliberately synthetic data. The UI must always label this session as demo.
    static func demo(now: Date = Date()) -> Self {
        .init(
            systems: [.init(id: "demo-home", name: "Casa", armed: true)],
            cameras: [
                .init(id: "demo-entrance", systemID: "demo-home", name: "Entrada", model: "Outdoor", status: "online", battery: "ok", wifiStrength: 4, temperatureCelsius: 19, motionEnabled: true, updatedAt: now),
                .init(id: "demo-garden", systemID: "demo-home", name: "Jardín", model: "Outdoor", status: "online", battery: "ok", wifiStrength: 3, temperatureCelsius: 17, motionEnabled: true, updatedAt: now),
                .init(id: "demo-living", systemID: "demo-home", name: "Salón", model: "Mini", status: "online", wifiStrength: 5, motionEnabled: false, updatedAt: now)
            ],
            clips: [
                .init(id: "demo-clip-1", cameraID: "demo-entrance", cameraName: "Entrada", createdAt: now.addingTimeInterval(-360), durationSeconds: 12),
                .init(id: "demo-clip-2", cameraID: "demo-garden", cameraName: "Jardín", createdAt: now.addingTimeInterval(-4200), durationSeconds: 8)
            ],
            refreshedAt: now
        )
    }
}
