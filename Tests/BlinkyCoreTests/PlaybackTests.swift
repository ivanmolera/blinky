import XCTest
@testable import BlinkyCore

final class PlaybackTests: XCTestCase {
    func testOnlyPrivateLoopbackHLSCanBePlayed() {
        let valid = "http://127.0.0.1:54321/random_capability_0123456789/stream.m3u8"
        XCTAssertNotNil(LocalPlaybackURL.validate(valid))
        for invalid in [
            "https://example.com/random_capability_0123456789/stream.m3u8",
            "http://192.168.1.10:54321/random_capability_0123456789/stream.m3u8",
            "http://127.0.0.1:54321/../stream.m3u8",
            valid + "?token=secret",
            valid.replacingOccurrences(of: "127.0.0.1", with: "user:password@127.0.0.1"),
            valid.replacingOccurrences(of: "stream.m3u8", with: "another-file.ts")
        ] { XCTAssertNil(LocalPlaybackURL.validate(invalid), invalid) }
    }

    func testRecordingWarningsAndStorageSourcesSurviveDecoding() throws {
        let json = #"{"systems":[],"cameras":[],"clips":[{"id":"local:1:2:3","cameraID":"1:default:4","cameraName":"Door","createdAt":"2026-11-25T20:00:00Z","downloaded":false,"source":"local","systemID":"1"}],"recordingWarnings":["Storage is unavailable"],"recordingsStatus":"partial","recordingsTruncated":true}"#
        let snapshot = try BridgeWire.decoder().decode(BlinkySnapshot.self, from: Data(json.utf8))
        XCTAssertEqual(snapshot.clips.first?.source, "local")
        XCTAssertEqual(snapshot.recordingWarnings, ["Storage is unavailable"])
        XCTAssertEqual(snapshot.recordingsStatus, "partial")
        XCTAssertEqual(snapshot.recordingsTruncated, true)
    }
}
