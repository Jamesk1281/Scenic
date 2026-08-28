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

    /// Tap target for the two corner buttons. Scaled, so the glyph inside still
    /// fits when the driver runs a larger system text size.
    @ScaledMetric(relativeTo: .body) private var controlSize: CGFloat = 30

    /// Height of the scenery-verdict buttons. Well over the 44 pt minimum,
    /// because these are meant to be hit by a driver who is not looking at them.
    @ScaledMetric(relativeTo: .body) private var verdictHeight: CGFloat = 52

    /// Watched so the drive trace can mark where the app went away and came
    /// back, and flush on the way out — a hole in the fixes otherwise looks the
    /// same as a tunnel.
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        Map(position: $camera) {
            MapPolyline(coordinates: nav.coordinates)
                .stroke(nav.followingFastest ? .gray : Color.scenic, lineWidth: 6)
            Marker("Destination", coordinate: nav.destination).tint(.red)
            UserAnnotation()
        }
        // North stops being obvious the moment the map turns with the car, and
        // on a scenic drive "which way am I actually pointing" is a question
        // worth answering without leaving the app. MapKit's own compass hides
        // itself at north-up and appears as soon as the map rotates, which in
        // a heading-up drive means it is simply always there.
        //
        // Placed by `.mapControls`, so it sits inside the map's safe area —
        // which the insets below have already pushed clear of the banner.
        .mapControls { MapCompass() }
        .ignoresSafeArea()
        .safeAreaInset(edge: .top) { banner }
        .safeAreaInset(edge: .bottom) { controls }
        .onAppear {
            locationManager.start()
            // Keep the screen awake for the whole drive, so the map stays
            // visible without the driver reaching for the phone. (Location
            // itself no longer depends on this: the app now declares
            // `UIBackgroundModes: location` and `LocationManager` opts into
            // background updates, so a locked phone keeps delivering fixes.
            // Before that, locking stopped the stream and the app appeared to
            // hang a couple of minutes into every drive.)
            UIApplication.shared.isIdleTimerDisabled = true
        }
        .onDisappear {
            locationManager.stop()
            UIApplication.shared.isIdleTimerDisabled = false
        }
        // Arrival ends the drive, so release the hardware then rather than
        // waiting for a tap on End. `nav.update` early-returns once `arrived`
        // latches, so nothing is being recorded or displayed — but the session
        // would otherwise keep running at 1 Hz, in the background, with the
        // screen held awake, until the driver happened to come back to the
        // phone. Locking it used to be the implicit backstop; declaring the
        // background mode is exactly what removed that.
        .onChange(of: nav.arrived) { _, arrived in
            guard arrived else { return }
            locationManager.stop()
            UIApplication.shared.isIdleTimerDisabled = false
        }
        // No `onChange` feeding `nav.update` here on purpose. The drive is wired
        // straight to CoreLocation in `RouteModel.startNavigation`, because a
        // view modifier stops firing the moment the phone locks — see
        // `LocationManager.onFix`. This view only draws what the drive decides.
        //
        // `scenePhase` is different, and is the right tool for exactly this: the
        // transition *into* the background is delivered, which is what the trace
        // needs to distinguish a suspended app from a tunnel.
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .background: nav.recordPhase("background")
            case .inactive:   nav.recordPhase("inactive")
            case .active:     nav.recordPhase("active")
            @unknown default: break
            }
        }
        .confirmationDialog("Switch to the fastest route?",
                            isPresented: $confirmingFastest,
                            titleVisibility: .visible) {
            Button("Switch to fastest", role: .destructive) {
                if let here = locationManager.location {
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
                HStack(spacing: 12) {
                    // The maneuver glyph, ahead of the words. A driver reads
                    // the arrow long before the sentence, and an exit should
                    // not look like a left turn.
                    Image(systemName: nav.currentSymbol)
                        .font(.title2.bold())
                        .foregroundStyle(Color.scenic)
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(distanceText(nav.distanceToNext))
                            .font(.subheadline).foregroundStyle(.secondary)
                        Text(nav.currentInstruction).font(.title3.bold())
                    }
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
        VStack(spacing: 8) {
            // Above the bar and outside its material, so it reads as a map
            // control rather than a trip control — and only while the map is
            // somewhere the driver put it, which is the only time it does
            // anything.
            if camera.positionedByUser {
                HStack {
                    Spacer()
                    recenterButton
                }
                .padding(.horizontal)
            }

            sceneryVerdict

            VStack(spacing: 6) {
                currentRoadLabel
                controlRow
                // Under the controls, not in the banner: the banner is where
                // the next maneuver goes, and no diagnostic outranks the turn
                // you are about to miss. Unmissable, but never in the way.
                if let problem = nav.recordingProblem {
                    Label(problem, systemImage: "exclamationmark.triangle.fill")
                        .font(.caption)
                        .foregroundStyle(.orange)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(.horizontal)
            .padding(.vertical, 10)
            .background(.ultraThinMaterial)
        }
    }

    /// The road under the car, across the top of the trip bar.
    ///
    /// Here rather than in the banner because the banner is for the maneuver
    /// ahead, and this is the opposite question — where am I *now*. It reads as
    /// a caption to the trip stats below it, which is about the attention it
    /// deserves: useful continuously, urgent never.
    ///
    /// The off-route wording is the point of the whole readout as much as the
    /// name is. `RouteModel.nameCurrentLocation` already spends a
    /// reverse-geocode on labelling the start, on the grounds that "My
    /// Location" alone gives the driver no way to notice we have put them on
    /// the wrong road; the same is true at 60 km/h, and this is the line that
    /// says so. A stale street name would be worse than nothing, so
    /// `NavigationModel.currentRoad` returns one only while the step list still
    /// describes where the car is.
    @ViewBuilder private var currentRoadLabel: some View {
        switch nav.currentRoad {
        case .named(let road):
            // One line, truncated rather than wrapped: a long road name must
            // not reflow the controls underneath it mid-drive.
            Text(road)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .truncationMode(.tail)
                .frame(maxWidth: .infinity)
                .accessibilityLabel("On \(road)")
        case .offRoute:
            Text("Off route")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.orange)
                .frame(maxWidth: .infinity)
                .accessibilityLabel("Off your route")
        case .unknown:
            EmptyView()
        }
    }

    /// The two buttons that make a drive worth taking twice.
    ///
    /// Everything else on this screen measures the car. These measure the road,
    /// and they are the only instrument in the project that can: the scenic score
    /// has been calibrated against its own distribution and against two byways
    /// named in `score.py`, which tests that it is self-consistent, not that it
    /// is right. Whether these roads are actually beautiful is a question only
    /// the person driving them can answer, and only while they are there.
    ///
    /// Sized and placed for a driver, which drove every choice here:
    ///
    /// - **Two targets, no scale.** A five-point rating needs aiming and aiming
    ///   needs looking. The calibration wants a rank statistic over many marks
    ///   anyway, so precision on any single one buys nothing.
    /// - **Above the trip bar, hard against the edges.** `tripStats` sits in the
    ///   middle because that is where a thumb rests — the same reasoning that
    ///   keeps the numbers untappable puts these where a resting hand isn't. The
    ///   `End` button is small, bordered and in the corner *below* them, so the
    ///   destructive control and the frequent one never sit side by side.
    /// - **Haptics that differ.** `success` for nice and `warning` for dull are
    ///   distinct patterns, so the driver feels *which* one they hit and never
    ///   has to look up to check. This is the whole confirmation; there is no
    ///   toast to read and no count to watch.
    ///
    /// Only while there is something to judge: before joining the route the
    /// driver is on some other road entirely, and after arriving they are parked.
    @ViewBuilder private var sceneryVerdict: some View {
        if nav.hasJoinedRoute && !nav.arrived && nav.canRecordMarks {
            HStack(spacing: 12) {
                verdictButton(.nice, symbol: "hand.thumbsup.fill",
                              tint: Color.scenic, label: "Lovely road")
                verdictButton(.dull, symbol: "hand.thumbsdown.fill",
                              tint: .secondary, label: "Nothing to see")
            }
            .padding(.horizontal)
        }
    }

    private func verdictButton(_ verdict: SceneryVerdict, symbol: String,
                               tint: Color, label: String) -> some View {
        Button {
            nav.mark(verdict)
            // Distinct patterns per verdict — the point is to be told apart by
            // feel. Generated fresh rather than kept around: a driver taps a
            // handful of times in an hour, so there is nothing to warm up for,
            // and holding one costs a strong reference for the whole drive.
            UINotificationFeedbackGenerator()
                .notificationOccurred(verdict == .nice ? .success : .warning)
        } label: {
            Image(systemName: symbol)
                .font(.title3)
                .foregroundStyle(tint)
                // Tall, and as wide as half the screen. The generous frame *is*
                // the feature: this has to be hittable without aiming.
                .frame(maxWidth: .infinity)
                .frame(height: verdictHeight)
                .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
        .accessibilityHint("Records how this stretch of road looks, for calibrating the scenic score")
    }

    /// Puts the camera back on the driver, heading-up.
    ///
    /// Without it, panning the map is a one-way door: `MapCameraPosition` stops
    /// following the moment the user drags it, and nothing here ever set it
    /// back — so a driver who nudged the map to see what was coming spent the
    /// rest of the drive with a map that no longer tracked them, and no way
    /// short of ending the drive to get it back.
    ///
    /// It restores `followsHeading` rather than merely centring, because
    /// heading-up is what the drive started in; recentring to a north-up map
    /// mid-drive would be its own surprise.
    private var recenterButton: some View {
        Button {
            withAnimation {
                camera = .userLocation(followsHeading: true, fallback: .automatic)
            }
        } label: {
            Label("Recenter", systemImage: "location.fill")
                .labelStyle(.iconOnly)
                .frame(width: controlSize, height: controlSize)
        }
        .buttonStyle(.borderedProminent)
        .clipShape(Circle())
        .accessibilityLabel("Recenter the map on your location")
    }

    private var controlRow: some View {
        HStack(alignment: .center) {
            Button(role: .destructive) { onEnd() } label: {
                Label("End", systemImage: "xmark").labelStyle(.iconOnly)
                    .frame(width: controlSize, height: controlSize)
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
                        .frame(width: controlSize, height: controlSize)
                }
                .buttonStyle(.bordered)
                .tint(.secondary)
                .accessibilityLabel("Switch to the fastest route")
            } else {
                // Keep the stats centered when the button isn't there.
                Color.clear.frame(width: controlSize, height: controlSize)
            }
        }
    }

    /// Arrival time, time left, distance left — the three numbers a driver
    /// actually watches.
    private var tripStats: some View {
        VStack(spacing: 1) {
            HStack(spacing: 5) {
                // A drive is unrepeatable — that light was that colour, that
                // traffic was that thick, once — so whether it is being recorded
                // has to be answerable at a glance, before pulling away rather
                // than after getting home. Silence would look identical to
                // working.
                Image(systemName: nav.recordingProblem == nil
                      ? "record.circle" : "exclamationmark.triangle.fill")
                    .font(.caption2)
                    .foregroundStyle(nav.recordingProblem == nil ? .red : .orange)
                Text(nav.eta, format: .dateTime.hour().minute())
                    .font(.title3.bold())
                    .monospacedDigit()
                // How many verdicts have been logged. The haptic is the primary
                // confirmation, but a driver who has haptics switched off system
                // wide — or is in Low Power Mode — gets nothing back from a tap
                // at all, and would have no way to tell a registered verdict
                // from a missed button until they got home. Small, and outside
                // the tap targets, because it is a receipt rather than a number
                // anybody drives by.
                if nav.marksRecorded > 0 {
                    Text("\(nav.marksRecorded)")
                        .font(.caption2.bold())
                        .monospacedDigit()
                        .foregroundStyle(Color.scenic)
                        .contentTransition(.numericText())
                }
            }
            .animation(.snappy, value: nav.marksRecorded)
            Text("\(timeText(nav.remainingMinutes)) · \(milesText(nav.remainingMeters))")
                .font(.caption)
                .foregroundStyle(.secondary)
                .monospacedDigit()
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            "Arriving at \(nav.eta.formatted(date: .omitted, time: .shortened)), "
            + "\(timeText(nav.remainingMinutes)) and \(milesText(nav.remainingMeters)) to go. "
            + (nav.recordingProblem ?? "Recording this drive.")
            + (nav.marksRecorded > 0 ? " \(nav.marksRecorded) scenery marks logged." : "")
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
