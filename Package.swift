// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "Blinky",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "Blinky", targets: ["Blinky"])],
    targets: [
        .target(name: "BlinkyCore"),
        .executableTarget(
            name: "Blinky",
            dependencies: ["BlinkyCore"],
            resources: [.copy("Bridge")],
            linkerSettings: [.linkedFramework("Security")]
        ),
        .testTarget(name: "BlinkyCoreTests", dependencies: ["BlinkyCore"])
    ]
)
