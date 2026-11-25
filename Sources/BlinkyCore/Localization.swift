import Foundation

public enum AppLanguage: String, CaseIterable, Identifiable, Codable, Sendable {
    case english = "en"
    case spanish = "es"
    case catalan = "ca"

    public var id: String { rawValue }
    public var locale: Locale { Locale(identifier: rawValue) }
    public var displayName: String {
        switch self {
        case .english: return "English"
        case .spanish: return "Español"
        case .catalan: return "Català"
        }
    }

    public static func preferred(from languages: [String] = Locale.preferredLanguages) -> Self {
        for language in languages {
            let code = language.lowercased().split(whereSeparator: { $0 == "-" || $0 == "_" }).first.map(String.init) ?? ""
            if let supported = Self(rawValue: code) { return supported }
        }
        return .english
    }
}

public enum AppStrings {
    public static func text(_ key: String, language: AppLanguage) -> String {
        let translated = localized(key, language: language)
        if translated != key || language == .spanish { return translated }
        // Bridge messages remain canonical until rendered, so changing language also
        // updates an existing status or error without repeating any camera action.
        let templates: [(String, String)] = [
            (#"^Clip guardado en (.+)\.$"#, "Clip guardado en %@."),
            (#"^Espera ([0-9]+) segundos antes de repetir esta acción\.$"#, "Espera %@ segundos antes de repetir esta acción."),
            (#"^Blink ha rechazado la acción \(código ([0-9]+)\)\. La cámara o el sistema pueden estar ocupados\.$"#, "Blink ha rechazado la acción (código %@). La cámara o el sistema pueden estar ocupados."),
            (#"^No se ha podido acceder al llavero de macOS \(([^)]+)\)\. Comprueba que esté desbloqueado\.$"#, "No se ha podido acceder al llavero de macOS (%@). Comprueba que esté desbloqueado.")
        ]
        for (pattern, template) in templates {
            guard let expression = try? NSRegularExpression(pattern: pattern),
                  let match = expression.firstMatch(in: key, range: NSRange(key.startIndex..., in: key)),
                  let range = Range(match.range(at: 1), in: key) else { continue }
            return format(template, language: language, String(key[range]))
        }
        return translated
    }

    public static func format(_ key: String, language: AppLanguage, _ arguments: CVarArg...) -> String {
        String(format: localized(key, language: language), locale: language.locale, arguments: arguments)
    }

    public static func translations(for language: AppLanguage) -> [String: String] {
        guard let path = Bundle.module.path(forResource: language.rawValue, ofType: "lproj"),
              let data = try? Data(contentsOf: URL(fileURLWithPath: path).appendingPathComponent("Localizable.strings")),
              let values = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: String] else { return [:] }
        return values
    }

    private static func localized(_ key: String, language: AppLanguage) -> String {
        guard let path = Bundle.module.path(forResource: language.rawValue, ofType: "lproj"), let bundle = Bundle(path: path) else { return key }
        return bundle.localizedString(forKey: key, value: key, table: "Localizable")
    }
}
