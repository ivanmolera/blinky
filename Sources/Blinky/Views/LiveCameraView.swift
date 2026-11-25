import AVKit
import BlinkyCore
import SwiftUI

struct LiveCameraView: View {
    @ObservedObject var store: AppStore
    let camera: BlinkCamera
    @State private var player: AVPlayer?
    @State private var failureObservation: NSKeyValueObservation?

    private var session: LiveSession? {
        store.liveCameraID == camera.id ? store.liveSession : nil
    }

    var body: some View {
        ZStack {
            if let player, session != nil {
                LivePlayerView(player: player)
            } else {
                CameraImage(camera: camera, isDemo: store.isDemo, language: store.language)
                if store.isStartingLive, store.liveCameraID == camera.id {
                    VStack(spacing: 12) {
                        ProgressView().controlSize(.large)
                        Text(store.text("Conectando con la cámara…"))
                            .font(.subheadline)
                    }
                    .padding(24)
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
                }
            }
        }
        .aspectRatio(16 / 9, contentMode: .fit)
        .background(.black.opacity(0.03))
        .overlay(alignment: .topLeading) {
            if session != nil {
                Label(store.text("Directo"), systemImage: "video.fill")
                    .font(.caption.weight(.semibold))
                    .padding(8)
                    .background(.regularMaterial, in: Capsule())
                    .padding(12)
            }
        }
        .onChange(of: session?.sessionID) { _, _ in updatePlayer() }
        .onAppear { updatePlayer() }
        .onDisappear {
            player?.pause()
            failureObservation = nil
            player = nil
            Task { await store.stopLive() }
        }
    }

    private func updatePlayer() {
        player?.pause()
        failureObservation = nil
        guard let session, let url = session.playbackURL else { player = nil; return }
        let sessionID = session.sessionID
        let item = AVPlayerItem(url: url)
        item.preferredForwardBufferDuration = 1
        failureObservation = item.observe(\.status, options: [.new]) { item, _ in
            if item.status == .failed {
                Task { @MainActor in await store.livePlaybackFailed(sessionID: sessionID) }
            }
        }
        player = AVPlayer(playerItem: item)
    }
}

// Use AppKit's player view directly because SwiftUI's VideoPlayer can abort when mounted on macOS.
private struct LivePlayerView: NSViewRepresentable {
    let player: AVPlayer

    func makeNSView(context: Context) -> AVPlayerView {
        let view = AVPlayerView()
        view.controlsStyle = .inline
        view.videoGravity = .resizeAspect
        view.allowsVideoFrameAnalysis = false
        attachPlayer(to: view)
        return view
    }

    func updateNSView(_ view: AVPlayerView, context: Context) {
        if view.player !== player { attachPlayer(to: view) }
    }

    static func dismantleNSView(_ view: AVPlayerView, coordinator: ()) {
        view.player = nil
    }

    private func attachPlayer(to view: AVPlayerView) {
        view.player = player
        player.play()
    }
}
