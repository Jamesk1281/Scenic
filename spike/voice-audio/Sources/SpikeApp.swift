import SwiftUI

@main
struct SpikeApp: App {
    init() { SpikeRunner.shared.start() }
    var body: some Scene { WindowGroup { SpikeView() } }
}

/// Deliberately trivial. Nothing here may be needed for the spike to keep
/// working — a view that has to be on screen is the mistake being tested for.
struct SpikeView: View {
    var body: some View {
        VStack(spacing: 12) {
            Text("Voice spike").font(.largeTitle.bold())
            Text(SpikeRunner.shared.declaredModes.joined(separator: " + "))
                .font(.title3).foregroundStyle(.secondary)
            Text(Bundle.main.bundleIdentifier ?? "").font(.caption)
            Text("Speaks every 12 s off the location stream.")
                .font(.footnote).multilineTextAlignment(.center)
        }
        .padding()
    }
}
