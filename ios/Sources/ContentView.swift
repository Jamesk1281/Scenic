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

    /// How tall the sheet is. Compact (just the search controls) until a route
    /// exists, then it rises to show the comparison.
    @State private var sheetHeight: PresentationDetent = .height(260)

    /// Owns the user's location; only started once navigation begins.
    @State private var locationManager = LocationManager()

    var body: some View {
        // A live navigation session takes over the whole screen; otherwise we
        // show route planning. The two cross-fade — a plain dissolve (in a
        // ZStack so they overlap mid-fade), so it doesn't fight the planning
        // sheet sliding away underneath.
        ZStack {
            if let nav = model.nav {
                NavView(nav: nav, locationManager: locationManager) { model.endNavigation() }
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
        }
        .ignoresSafeArea()
        // Refit the camera whenever a new scenic route arrives.
        .onChange(of: model.response?.scenic.coordinates.count) { frameRoute() }
        // Raise the sheet to show results when a route appears; lower it when cleared.
        .onChange(of: model.response == nil) { _, noRoute in
            sheetHeight = noRoute ? .height(260) : .medium
        }
        .sheet(isPresented: .constant(true)) {
            RoutePanel(model: model)
                .presentationDetents([.height(260), .medium, .large], selection: $sheetHeight)
                .presentationBackgroundInteraction(.enabled(upThrough: .medium))
                .presentationDragIndicator(.visible)
                .interactiveDismissDisabled()   // it's the main UI — never dismiss
        }
    }

    /// Fit the scenic route into the map, biased toward the top so the sheet
    /// doesn't cover it. The sheet is draggable, so this only needs to be
    /// roughly right — no per-device tuning.
    private func frameRoute() {
        guard let coords = model.response?.scenic.coordinates, !coords.isEmpty else { return }

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
