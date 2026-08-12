import MapKit
import SwiftUI
import UIKit

/// The live navigation screen: a heading-up map that follows the driver, the
/// next maneuver up top, and a trip bar at the bottom (arrival time, time left,
/// distance left) with the drive controls either side of it.
struct NavView: View {
    @Bindable var nav: NavigationModel
    let locationManager: LocationManager
    var onEnd: () -> Void

    /// Follows the user's location and turns with their heading, like any
    /// turn-by-turn map. Falls back to a sensible frame before the first fix.
    @State private var camera: MapCameraPosition =
        .userLocation(followsHeading: true, fallback: .automatic)

    /// Whether the "switch to fastest?" confirmation is up.
    @State private var confirmingFastest = false

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
        .confirmationDialog("Switch to the fastest route?",
                            isPresented: $confirmingFastest,
                            titleVisibility: .visible) {
            Button("Switch to fastest", role: .destructive) {
                if let here = locationManager.location?.coordinate {
                    Task { await nav.switchToFastest(from: here) }
                }
            }
            Button("Keep the scenic route", role: .cancel) {}
        } message: {
            Text("This gives up the scenic route for the rest of the drive.")
        }
    }

    /// The maneuver banner — distance + instruction, or whatever else the driver
    /// most needs to know right now.
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
            } else if !nav.hasJoinedRoute {
                // The trip was planned from somewhere the driver isn't yet. Say
                // so plainly rather than reading out a first instruction that
                // belongs to a road miles away.
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(distanceText(nav.distanceToRouteStart)) away")
                        .font(.subheadline).foregroundStyle(.secondary)
                    Text("Head to the start of your route").font(.title3.bold())
                }
                .frame(maxWidth: .infinity, alignment: .leading)
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

    /// Trip stats front and center, with End and the fastest-route escape hatch
    /// tucked into the corners.
    ///
    /// The stats take the middle on purpose. That's where a thumb lands, and the
    /// numbers aren't tappable — so resting a hand on the phone mid-drive can't
    /// trigger anything.
    private var controls: some View {
        HStack(alignment: .center) {
            Button(role: .destructive) { onEnd() } label: {
                Label("End", systemImage: "xmark").labelStyle(.iconOnly)
                    .frame(width: 30, height: 30)
            }
            .buttonStyle(.bordered)
            .accessibilityLabel("End the drive")

            Spacer(minLength: 8)

            if nav.arrived {
                Text("Arrived").font(.headline)
            } else {
                tripStats
            }

            Spacer(minLength: 8)

            // Hidden once taken: there's no second fastest route to switch to.
            if !nav.followingFastest && !nav.arrived {
                Button { confirmingFastest = true } label: {
                    Label("Fastest", systemImage: "bolt.fill").labelStyle(.iconOnly)
                        .frame(width: 30, height: 30)
                }
                .buttonStyle(.bordered)
                .tint(.secondary)
                .accessibilityLabel("Switch to the fastest route")
            } else {
                // Keep the stats centered when the button isn't there.
                Color.clear.frame(width: 30, height: 30)
            }
        }
        .padding(.horizontal)
        .padding(.vertical, 10)
        .background(.ultraThinMaterial)
    }

    /// Arrival time, time left, distance left — the three numbers a driver
    /// actually watches.
    private var tripStats: some View {
        VStack(spacing: 1) {
            Text(nav.eta, format: .dateTime.hour().minute())
                .font(.title3.bold())
                .monospacedDigit()
            Text("\(timeText(nav.remainingMinutes)) · \(milesText(nav.remainingMeters))")
                .font(.caption)
                .foregroundStyle(.secondary)
                .monospacedDigit()
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            "Arriving at \(nav.eta.formatted(date: .omitted, time: .shortened)), "
            + "\(timeText(nav.remainingMinutes)) and \(milesText(nav.remainingMeters)) to go"
        )
    }

    /// Distance in friendly US units: feet (rounded to 50) up close, miles after.
    private func distanceText(_ meters: Double) -> String {
        let feet = meters * 3.28084
        if feet < 1000 {
            return "\(max(50, Int((feet / 50).rounded()) * 50)) ft"
        }
        return milesText(meters)
    }

    /// "0.4 mi" up close, "23 mi" once the decimal stops meaning anything.
    private func milesText(_ meters: Double) -> String {
        let miles = meters / 1609.34
        return miles < 10 ? String(format: "%.1f mi", miles) : "\(Int(miles.rounded())) mi"
    }

    /// "8 min", or "1 hr 12 min" once it's worth splitting.
    private func timeText(_ minutes: Double) -> String {
        let total = max(0, Int(minutes.rounded()))
        return total >= 60 ? "\(total / 60) hr \(total % 60) min" : "\(total) min"
    }
}
