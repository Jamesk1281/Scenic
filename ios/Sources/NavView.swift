import MapKit
import SwiftUI
import UIKit

/// The live navigation screen: a heading-up map that follows the driver, with
/// the next maneuver up top and controls (bail to the fastest route, end the
/// drive) at the bottom.
struct NavView: View {
    @Bindable var nav: NavigationModel
    let locationManager: LocationManager
    var onEnd: () -> Void

    /// Follows the user's location and turns with their heading, like any
    /// turn-by-turn map. Falls back to a sensible frame before the first fix.
    @State private var camera: MapCameraPosition =
        .userLocation(followsHeading: true, fallback: .automatic)

    var body: some View {
        Map(position: $camera) {
            MapPolyline(coordinates: nav.route.coordinates)
                .stroke(nav.followingFastest ? .gray : Color.scenic, lineWidth: 6)
            Marker("Destination", coordinate: nav.destination).tint(.red)
            UserAnnotation()
        }
        .ignoresSafeArea()
        .safeAreaInset(edge: .top) { banner }
        .safeAreaInset(edge: .bottom) { controls }
        .onAppear {
            locationManager.start()
            // Keep the screen awake for the whole drive. iOS otherwise dims and
            // locks the display after a minute or two without a touch — and
            // since we hold only when-in-use authorization and declare no
            // background location mode, locking silently stops the location
            // updates that `nav.update` runs on. Steps would stop advancing,
            // off-route detection would stop, and arrival would never fire:
            // the app appears to hang a couple of minutes into every drive.
            UIApplication.shared.isIdleTimerDisabled = true
        }
        .onDisappear {
            locationManager.stop()
            UIApplication.shared.isIdleTimerDisabled = false
        }
        // CLLocation isn't Equatable, so we watch the fix's timestamp and read
        // the location itself when it changes.
        .onChange(of: locationManager.location?.timestamp) {
            if let location = locationManager.location { nav.update(location) }
        }
    }

    /// The maneuver banner — distance + instruction, or an arrival/reroute note.
    @ViewBuilder private var banner: some View {
        Group {
            if nav.arrived {
                Text("You've arrived 🎉").font(.title2.bold())
                    .frame(maxWidth: .infinity)
            } else if locationManager.authorization == .denied
                        || locationManager.authorization == .restricted {
                // Without location we can't follow the drive at all — say so
                // instead of sitting silently on the first instruction.
                Label("Location access is off — allow it in Settings to navigate.",
                      systemImage: "location.slash")
                    .font(.subheadline).frame(maxWidth: .infinity)
            } else if nav.isRerouting {
                Label("Rerouting…", systemImage: "arrow.triangle.2.circlepath")
                    .font(.headline).frame(maxWidth: .infinity)
            } else {
                VStack(alignment: .leading, spacing: 2) {
                    Text(distanceText(nav.distanceToNext))
                        .font(.subheadline).foregroundStyle(.secondary)
                    Text(nav.currentInstruction).font(.title3.bold())
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding()
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16))
        .padding(.horizontal)
    }

    /// End the drive, and (until you've bailed) a switch-to-fastest button for
    /// when you realize you're running late.
    private var controls: some View {
        HStack {
            Button(role: .destructive) { onEnd() } label: {
                Label("End", systemImage: "xmark")
            }
            .buttonStyle(.bordered)

            Spacer()

            if !nav.followingFastest && !nav.arrived {
                Button {
                    if let here = locationManager.location?.coordinate {
                        Task { await nav.switchToFastest(from: here) }
                    }
                } label: {
                    Label("Fastest route", systemImage: "bolt.fill")
                }
                .buttonStyle(.borderedProminent)
                .tint(.gray)
            }
        }
        .padding(.horizontal)
        .padding(.vertical, 10)
        .background(.ultraThinMaterial)
    }

    /// Distance in friendly US units: feet (rounded to 50) up close, miles after.
    private func distanceText(_ meters: Double) -> String {
        let feet = meters * 3.28084
        if feet < 1000 {
            return "\(max(50, Int((feet / 50).rounded()) * 50)) ft"
        }
        return String(format: "%.1f mi", meters / 1609.34)
    }
}
