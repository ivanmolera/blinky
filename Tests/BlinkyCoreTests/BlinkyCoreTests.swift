import XCTest
@testable import BlinkyCore

final class BlinkyCoreTests: XCTestCase {
    func testRequestKeepsPasswordInJSONAndEscapesNewlines() throws {
        let data = try BridgeWire.request(id: "request-1", method: "login", params: ["password": .string("a\nb\"c"), "username": .string("example@example.com")])
        XCTAssertEqual(data.filter { $0 == 10 }.count, 1, "One JSONL request must occupy exactly one line")
        let request = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let params = try XCTUnwrap(request["params"] as? [String: Any])
        XCTAssertEqual(params["password"] as? String, "a\nb\"c")
    }

    func testSnapshotAcceptsUnknownStatesAndFractionalDates() throws {
        let json = #"{"id":"1","result":{"systems":[{"id":"s","name":"Casa","armed":null}],"cameras":[{"id":"c","systemID":"s","name":"Entrada","model":"Outdoor","status":"unknown","motionEnabled":null,"updatedAt":"2026-11-25T12:30:40.125Z"}],"clips":[{"id":"v","cameraID":"c","cameraName":"Entrada","createdAt":"2026-11-25T12:30:40Z","downloaded":false}],"refreshedAt":"2026-11-25T12:31:00Z"}}"#
        let snapshot = try BridgeWire.response(Data(json.utf8), id: "1", as: BlinkySnapshot.self)
        XCTAssertNil(snapshot.systems[0].armed)
        XCTAssertNil(snapshot.cameras[0].motionEnabled)
        XCTAssertNotNil(snapshot.cameras[0].updatedAt)
        XCTAssertEqual(snapshot.clips.count, 1)
    }

    func testResponseRejectsOtherRequestsAndSurfacesRemoteFailure() throws {
        XCTAssertThrowsError(try BridgeWire.response(Data(#"{"id":"other","result":{}}"#.utf8), id: "expected", as: EmptyResult.self))
        let errorJSON = Data(#"{"id":"1","error":{"code":"authentication_required","message":"Conecta tu cuenta."}}"#.utf8)
        XCTAssertThrowsError(try BridgeWire.response(errorJSON, id: "1", as: EmptyResult.self)) { error in
            XCTAssertEqual((error as? BridgeFailure)?.code, "authentication_required")
            XCTAssertEqual(error.localizedDescription, "Conecta tu cuenta.")
        }
    }

    func testClipNamesCannotEscapeTheSelectedFolder() {
        XCTAssertEqual(SafeFilename.clip("../../private/movie.mp4"), "movie.mp4")
        XCTAssertEqual(SafeFilename.clip("..\\..\\movie"), "movie.mp4")
        XCTAssertEqual(SafeFilename.clip(".."), "clip.mp4")
        XCTAssertFalse(SafeFilename.clip("clip\n.mp4").contains("\n"))
    }

    func testDemoIDsResolveToExistingSystemsAndCameras() {
        let snapshot = BlinkySnapshot.demo(now: Date(timeIntervalSince1970: 0))
        let systems = Set(snapshot.systems.map(\.id))
        let cameras = Set(snapshot.cameras.map(\.id))
        XCTAssertTrue(snapshot.cameras.allSatisfy { systems.contains($0.systemID) })
        XCTAssertTrue(snapshot.clips.allSatisfy { cameras.contains($0.cameraID) })
        XCTAssertTrue(snapshot.cameras.allSatisfy { $0.thumbnailBase64 == nil })
    }

    func testTransportSerializesConcurrentRequestsAndPreservesProcessAfterRemoteError() async throws {
        let script = """
        import sys, json
        for line in sys.stdin:
            request = json.loads(line)
            if request['method'] == 'fail':
                print(json.dumps({'id': request['id'], 'error': {'code': 'test', 'message': 'Expected failure'}}), flush=True)
            else:
                print(json.dumps({'id': request['id'], 'result': {'bridgeVersion': '1', 'pythonVersion': 'test', 'blinkpyAvailable': False}}), flush=True)
        """
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("blinky-test-\(UUID().uuidString).py")
        try script.write(to: url, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: url) }
        let bridge = BridgeClient(pythonURL: URL(fileURLWithPath: "/usr/bin/python3"), scriptURL: url, timeout: 5)
        do {
            let _: EmptyResult = try await bridge.call("fail")
            XCTFail("Expected a remote error")
        } catch { XCTAssertTrue(error is BridgeFailure) }
        async let first: BridgeHealth = bridge.call("health")
        async let second: BridgeHealth = bridge.call("health")
        let results = try await [first, second]
        XCTAssertEqual(results.count, 2)
        XCTAssertTrue(results.allSatisfy { $0.bridgeVersion == "1" })
        await bridge.stop()
    }

    func testUnresponsiveBridgeTimesOut() async throws {
        let script = "import sys, time\nsys.stdin.readline()\ntime.sleep(10)\n"
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("blinky-timeout-\(UUID().uuidString).py")
        try script.write(to: url, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: url) }
        let bridge = BridgeClient(pythonURL: URL(fileURLWithPath: "/usr/bin/python3"), scriptURL: url, timeout: 0.1)
        do {
            let _: EmptyResult = try await bridge.call("health")
            XCTFail("Expected a timeout")
        } catch {
            guard case BridgeClientError.timeout = error else {
                await bridge.stop()
                return XCTFail("Unexpected error type: \(type(of: error))")
            }
        }
        await bridge.stop()
    }
}
