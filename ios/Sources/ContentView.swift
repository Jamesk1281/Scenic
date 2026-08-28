import MapKit
import SwiftUI

/// The whole screen. Two modes that animate between each other:
///   • planning — a full-bleed map with the route-planning bottom sheet
///   • navigating — the live turn-by-turn screen (NavView)
///
/// Using a real `.sheet` for planning means the OS handles all the fiddly parts
/// — sizing, scrolling, dragging between heights, lifting out of the keyboard's
/// way — so there's no manual layout math here.
struct ContentView: View {
    @State private var model = RouteModel()

    /// The map camera. Starts framed on Massachusetts; we refit it when a route
    /// comes back.
    @State private var camera: MapCameraPosition = .region(.massachusetts)

    /// How tall the sheet is. Compact until the user engages with it; `RoutePanel`
    /// raises and lowers it from there.
    @State private var sheetHeight: PresentationDetent = .planningCompact

    var body: some View {
        // A live navigation session takes over the whole screen; otherwise we
        // show route planning. The two cross-fade — a plain dissolve (in a
        // ZStack so they overlap mid-fade), so it doesn't fight the planning
        // sheet sliding away underneath.
        ZStack {
            if let nav = model.nav {
                NavView(nav: nav, locationManager: model.locationManager) { model.endNavigation() }
                    .transition(.opacity)
            } else {
                planningView
                    .transition(.opacity)
            }
        }
        .animation(.smooth(duration: 0.35), value: model.nav != nil)
    }

    private var planningView: some View {
        Map(position: $camera) {
            switch model.mode {
            case .directions:
                // Fastest route (gray, dashed) sits under the scenic route (green).
                if let fastest = model.response?.fastest {
                    MapPolyline(coordinates: fastest.coordinates)
                        .stroke(.gray, style: StrokeStyle(lineWidth: 4, dash: [6, 5]))
                }
                if let scenic = model.response?.scenic {
                    MapPolyline(coordinates: scenic.coordinates)
                        .stroke(Color.scenic, lineWidth: 6)
                }
                if let start = model.start {
                    Marker("Start", coordinate: start).tint(.green)
                }
                if let end = model.end {
                    Marker("End", coordinate: end).tint(.red)
                }
            case .loops:
                // One closed line, and the far point marked — which is the only
                // thing a loop has to say about its shape that the line doesn't.
                if let loop = model.loops.response?.loop {
                    MapPolyline(coordinates: loop.coordinates)
                        .stroke(Color.scenic, lineWidth: 6)
                }
                if let start = model.loops.start {
                    Marker("Start and finish", coordinate: start).tint(.green)
                }
                if let turnaround = model.loops.response?.meta.turnaroundCoordinate {
                    Marker("Turnaround", systemImage: "arrow.uturn.left",
                           coordinate: turnaround).tint(.orange)
                }
            }
            UserAnnotation()
        }
        .mapControls { MapUserLocationButton() }
        .ignoresSafeArea()
        // Rank search results around whatever the user is looking at. Apple's
        // search sorts by distance from this region's center, so without it
        // every search is answered from the middle of the state.
        .onMapCameraChange(frequency: .onEnd) { context in
            model.searchRegion = context.region
        }
        // Refit the camera whenever a new scenic route arrives.
        .onChange(of: model.response?.scenic.coordinates.count) { frameRoute() }
        // ...and whenever a new loop does, or the user switches which one they
        // are looking at. Without the mode change the map keeps the other tab's
        // framing, which on a loop of a different size reads as a broken draw.
        .onChange(of: model.loops.response?.loop.coordinates.count) { frameLine() }
        .onChange(of: model.mode) { frameLine() }
        // Setting a start with no destination yet (typically "My Location")
        // shows nothing on screen otherwise — and seeing the pin land on the
        // right street is how you catch a bad fix before pulling away.
        .onChange(of: model.start?.latitude) {
            guard model.response == nil, let start = model.start else { return }
            withAnimation { camera = .region(.around(start, meters: 1_200)) }
        }
        // The same for a loop start, which is the only pin that tab has, so
        // seeing it land on the right street matters just as much.
        .onChange(of: model.loops.start?.latitude) {
            guard model.loops.response == nil, let start = model.loops.start else { return }
            withAnimation { camera = .region(.around(start, meters: 1_200)) }
        }
        .sheet(isPresented: .constant(true)) {
            RoutePanel(model: model, detent: $sheetHeight)
                .presentationDetents([.planningCompact, .medium, .large], selection: $sheetHeight)
                .presentationBackgroundInteraction(.enabled(upThrough: .medium))
                .presentationDragIndicator(.visible)
                .interactiveDismissDisabled()   // it's the main UI — never dismiss
        }
    }

    /// Fit the scenic route into the map, biased toward the top so the sheet
    /// doesn't cover it. The sheet is draggable, so this only needs to be
    /// roughly right — no per-device tuning.
    private func frameRoute() {
        frame(model.response?.scenic.coordinates)
    }

    /// Fit whichever line the current mode is showing.
    private func frameLine() {
        switch model.mode {
        case .directions: frame(model.response?.scenic.coordinates)
        case .loops:      frame(model.loops.response?.loop.coordinates)
        }
    }

    private func frame(_ coordinates: [CLLocationCoordinate2D]?) {
        guard let coords = coordinates, !coords.isEmpty else { return }

        // Smallest rectangle (in MapKit's projected plane) containing the route.
        var rect = MKMapRect.null
        for coord in coords {
            let point = MKMapPoint(coord)
            rect = rect.union(MKMapRect(x: point.x, y: point.y, width: 0, height: 0))
        }

        // Pad the sides/top a little and the bottom a lot, so MapKit centers a
        // rect whose route sits in the upper half — clear of the sheet. The big
        // bottom margin scales with the larger dimension so wide east-west
        // routes get lifted too, not just tall ones.
        let w = rect.size.width
        let h = rect.size.height
        let bottomLift = max(h * 1.4, w * 0.9)
        let framed = MKMapRect(
            x: rect.origin.x - w * 0.15,
            y: rect.origin.y - h * 0.20,
            width: w * 1.30,
            height: h * 1.20 + bottomLift
        )
        withAnimation { camera = .rect(framed) }
    }
}
