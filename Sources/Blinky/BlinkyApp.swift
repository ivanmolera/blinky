import AppKit
import SwiftUI

@main
struct BlinkyApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var store = AppStore()

    var body: some Scene {
        Window("Blinky", id: "main") {
            ContentView(store: store)
                .frame(minWidth: 900, minHeight: 650)
                .task { await store.bootstrap() }
        }
        .defaultSize(width: 1180, height: 790)
        .commands {
            CommandGroup(replacing: .newItem) { }
            CommandMenu("Cámaras") {
                Button("Actualizar") { Task { await store.refresh() } }
                    .keyboardShortcut("r", modifiers: .command)
                    .disabled(store.isBusy || (!store.isAuthenticated && !store.isDemo))
            }
        }

        Settings {
            SettingsView(store: store)
                .frame(width: 640, height: 560)
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }
}
