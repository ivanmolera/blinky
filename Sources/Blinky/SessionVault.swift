import Foundation
import Security

/// Only refreshable session data is persisted; never the Blink password or MFA code.
struct SessionVault {
    private let service = "com.ivanmolera.blinky"
    private let account = "blink-session"

    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    func load() throws -> String? {
        var request = query
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data,
              let session = String(data: data, encoding: .utf8) else {
            throw VaultError(status: status)
        }
        return session
    }

    func save(_ session: String) throws {
        let data = Data(session.utf8)
        let attributes = [kSecValueData as String: data] as [String: Any]
        let status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            var item = query
            item[kSecValueData as String] = data
            item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            item[kSecAttrSynchronizable as String] = false
            let inserted = SecItemAdd(item as CFDictionary, nil)
            guard inserted == errSecSuccess else { throw VaultError(status: inserted) }
        } else if status != errSecSuccess {
            throw VaultError(status: status)
        }
    }

    func delete() throws {
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw VaultError(status: status) }
    }

    private struct VaultError: LocalizedError {
        let status: OSStatus
        var errorDescription: String? {
            "No se ha podido acceder al llavero de macOS (\(status)). Comprueba que esté desbloqueado."
        }
    }
}
