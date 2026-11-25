import Foundation

public enum JSONValue: Codable, Sendable, Equatable {
    case string(String), bool(Bool), number(Double), object([String: JSONValue]), array([JSONValue]), null

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() { self = .null }
        else if let value = try? container.decode(Bool.self) { self = .bool(value) }
        else if let value = try? container.decode(String.self) { self = .string(value) }
        else if let value = try? container.decode(Double.self) { self = .number(value) }
        else if let value = try? container.decode([String: JSONValue].self) { self = .object(value) }
        else { self = .array(try container.decode([JSONValue].self)) }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value): try container.encode(value)
        case .bool(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }
}

public struct BridgeFailure: Decodable, Sendable, LocalizedError {
    public let code: String
    public let message: String
    public var errorDescription: String? { message }
}

public enum BridgeClientError: LocalizedError {
    case runtimeUnavailable, scriptUnavailable, closed, timeout, invalidResponse, responseTooLarge

    public var errorDescription: String? {
        switch self {
        case .runtimeUnavailable: return "No se encuentra Python. Ejecuta scripts/bootstrap.sh o elige el intérprete en Ajustes."
        case .scriptUnavailable: return "No se encuentra el componente de conexión de Blinky. Vuelve a compilar la aplicación."
        case .closed: return "El componente de conexión se ha detenido. Vuelve a conectar."
        case .timeout: return "Blink no ha respondido a tiempo. Vuelve a intentarlo más tarde."
        case .invalidResponse: return "La respuesta del componente de conexión no es válida."
        case .responseTooLarge: return "La respuesta supera el tamaño admitido por esta versión."
        }
    }
}

private struct BridgeRequest: Encodable {
    let id: String
    let method: String
    let params: [String: JSONValue]
}

private struct BridgeResponse<Result: Decodable>: Decodable {
    let id: String?
    let result: Result?
    let error: BridgeFailure?
}

public enum BridgeWire {
    public static func request(id: String, method: String, params: [String: JSONValue]) throws -> Data {
        var data = try JSONEncoder().encode(BridgeRequest(id: id, method: method, params: params))
        data.append(0x0a)
        return data
    }

    public static func response<Result: Decodable>(_ data: Data, id: String, as type: Result.Type) throws -> Result {
        let response = try decoder().decode(BridgeResponse<Result>.self, from: data)
        guard response.id == id else { throw BridgeClientError.invalidResponse }
        if let error = response.error { throw error }
        guard let result = response.result else { throw BridgeClientError.invalidResponse }
        return result
    }

    public static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let text = try container.decode(String.self)
            let formatter = ISO8601DateFormatter()
            formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = formatter.date(from: text) { return date }
            formatter.formatOptions = [.withInternetDateTime]
            if let date = formatter.date(from: text) { return date }
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Expected an ISO 8601 timestamp")
        }
        return decoder
    }
}

public enum SafeFilename {
    public static func clip(_ value: String) -> String {
        let name = value.replacingOccurrences(of: "\\", with: "/").split(separator: "/").last.map(String.init) ?? "clip.mp4"
        let safe = name.unicodeScalars.filter { !CharacterSet.controlCharacters.contains($0) }.map(String.init).joined()
        guard safe != ".", safe != "..", !safe.isEmpty else { return "clip.mp4" }
        return safe.lowercased().hasSuffix(".mp4") ? safe : safe + ".mp4"
    }
}
