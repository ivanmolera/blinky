import Foundation
import XCTest
@testable import BlinkyCore

final class LocalizationTests: XCTestCase {
    func testSupportedLanguagesKeepTheirNativeNames() {
        XCTAssertEqual(AppLanguage.allCases.map(\.displayName), ["English", "Español", "Català"])
        XCTAssertEqual(AppLanguage.catalan.locale.identifier, "ca")
        XCTAssertEqual(AppLanguage.spanish.id, "es")
    }

    func testPreferredLanguageAcceptsRegionalIdentifiersAndFallsBackToEnglish() {
        XCTAssertEqual(AppLanguage.preferred(from: ["ca-ES", "en-US"]), .catalan)
        XCTAssertEqual(AppLanguage.preferred(from: ["ES_es"]), .spanish)
        XCTAssertEqual(AppLanguage.preferred(from: ["fr-FR", "en-GB"]), .english)
        XCTAssertEqual(AppLanguage.preferred(from: ["de-DE", "ca"]), .catalan)
        XCTAssertEqual(AppLanguage.preferred(from: ["fr-FR"]), .english)
        XCTAssertEqual(AppLanguage.preferred(from: []), .english)
    }

    func testAllCatalogsContainTheSameNonemptyKeys() {
        let baseline = AppStrings.translations(for: .spanish)
        XCTAssertFalse(baseline.isEmpty, "The localized resources must be included in the package bundle")
        let required = ["Idioma", "Ajustes", "Directo", "Nube", "USB", "Reintentar",
                        "No se pudieron cargar las grabaciones", "Iniciar directo",
                        "La sesión ha caducado. Vuelve a iniciar sesión."]
        for key in required { XCTAssertNotNil(baseline[key], key) }
        for language in AppLanguage.allCases {
            let translations = AppStrings.translations(for: language)
            XCTAssertEqual(Set(translations.keys), Set(baseline.keys), language.rawValue)
            XCTAssertTrue(translations.values.allSatisfy { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }, language.rawValue)
        }
    }

    func testTranslationsPreserveEveryFormatArgument() throws {
        let expression = try NSRegularExpression(pattern: #"%(?:[0-9]+\$)?[@dfsu]"#)
        func arguments(in value: String) -> [String] {
            expression.matches(in: value, range: NSRange(value.startIndex..., in: value)).compactMap {
                Range($0.range, in: value).map { String(value[$0]) }
            }
        }
        for language in AppLanguage.allCases {
            for (key, translation) in AppStrings.translations(for: language) {
                XCTAssertEqual(arguments(in: translation), arguments(in: key), "\(language.rawValue): \(key)")
            }
        }
    }

    func testInterfaceTranslationsAndUserContentFallback() {
        XCTAssertEqual(AppStrings.text("Ajustes", language: .english), "Settings")
        XCTAssertEqual(AppStrings.text("Ajustes", language: .catalan), "Configuració")
        XCTAssertEqual(AppStrings.text("Ajustes", language: .spanish), "Ajustes")
        XCTAssertEqual(AppStrings.text("Nube", language: .catalan), "Núvol")
        XCTAssertEqual(AppStrings.text("My front door camera", language: .catalan), "My front door camera")
    }

    func testExistingRuntimeMessagesTranslateWithoutRepeatingActions() {
        let wait = "Espera 12 segundos antes de repetir esta acción."
        XCTAssertEqual(AppStrings.text(wait, language: .english), "Wait 12 seconds before repeating this action.")
        XCTAssertEqual(AppStrings.text(wait, language: .catalan), "Espera 12 segons abans de repetir aquesta acció.")
        XCTAssertEqual(AppStrings.text(wait, language: .spanish), wait)
        XCTAssertEqual(AppStrings.text("Clip guardado en Entrada.mp4.", language: .english), "Clip saved to Entrada.mp4.")
        XCTAssertEqual(AppStrings.text("No se ha podido acceder al llavero de macOS (-25293). Comprueba que esté desbloqueado.", language: .catalan),
                       "No s’ha pogut accedir al clauer de macOS (-25293). Comprova que estigui desbloquejat.")
        let rejected = "Blink ha rechazado la acción (código 307). La cámara o el sistema pueden estar ocupados."
        XCTAssertEqual(AppStrings.text(rejected, language: .english),
                       "Blink rejected the action (code 307). The camera or system may be busy.")
        XCTAssertEqual(AppStrings.text(rejected, language: .catalan),
                       "Blink ha rebutjat l’acció (codi 307). La càmera o el sistema poden estar ocupats.")
        XCTAssertEqual(AppStrings.text("Detección habilitada. El sistema está desarmado; actívalo para registrar movimiento.", language: .english),
                       "Motion detection enabled. The system is disarmed; arm it to record movement.")
    }

    func testFormattedAccessibilityTextKeepsCameraAndDate() {
        XCTAssertEqual(AppStrings.format("Descargar clip de %@ del %@", language: .english, "Entrada", "25 Nov"),
                       "Download clip from Entrada recorded on 25 Nov")
        XCTAssertEqual(AppStrings.format("Activar sistema %@", language: .catalan, "Casa"), "Activar el sistema Casa")
    }
}
