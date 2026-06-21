import MapKit
import SwiftUI

/// The app's accent green — the scenic route line and highlights. Defined once
/// so every view uses the same color.
extension Color {
    static let scenic = Color(red: 0.22, green: 0.83, blue: 0.62)
}

// MARK: - Screen

/// The whole screen: a full-bleed map with a system bottom sheet on top.
///
/// Using a real `.sheet` (instead of a hand-built floating panel) means the OS
/// handles all the fiddly parts for us — sizing, scrolling, dragging between
/// heights, and lifting out of the keyboard's way. There is no manual layout
/// math here.
struct ContentView: View {
    @State private var model = RouteModel()

    /// The map camera. Starts framed on Massachusetts; we refit it when a route
    /// comes back.
    @State private var camera: MapCameraPosition = .region(
        MKCoordinateRegion(
            center: CLLocationCoordinate2D(latitude: 42.15, longitude: -71.8),
            span: MKCoordinateSpan(latitudeDelta: 2.6, longitudeDelta: 2.6)
        )
    )

    /// How tall the sheet is. Compact (just the search controls) until a route
    /// exists, then it rises to show the comparison.
    @State private var sheetHeight: PresentationDetent = .height(260)

    var body: some View {
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

// MARK: - Sheet content

/// The bottom-sheet panel: two address searches, the preference slider, and the
/// route comparison once both ends are set. It's a plain scrolling stack — the
/// sheet decides how much of it is visible.
struct RoutePanel: View {
    @Bindable var model: RouteModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                header
                searchField("Start address or place", $model.startQuery, dot: .green, role: .start)
                searchField("Destination address or place", $model.endQuery, dot: .red, role: .end)
                prefSlider

                if model.isLoading {
                    ProgressView().frame(maxWidth: .infinity)
                }
                if let error = model.errorText {
                    Text(error).font(.caption).foregroundStyle(.red)
                }
                if let response = model.response {
                    RouteResults(response: response)
                }
            }
            .padding(20)
        }
    }

    /// Title + a short hint, with swap/clear buttons once something is set.
    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 1) {
                Text("Scenic").font(.title2.bold())
                Text(model.response == nil
                     ? "Search for a start and destination"
                     : "Drag the slider to trade time for scenery")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if model.start != nil || model.end != nil {
                Button { model.swapEnds() } label: { Image(systemName: "arrow.up.arrow.down") }
                    .disabled(model.start == nil || model.end == nil)
                Button { model.clear() } label: { Image(systemName: "xmark.circle") }
            }
        }
    }

    /// One address row: a colored dot and a field that searches on submit.
    private func searchField(
        _ prompt: String, _ text: Binding<String>, dot: Color, role: Endpoint
    ) -> some View {
        HStack(spacing: 8) {
            Circle().fill(dot).frame(width: 9, height: 9)
            TextField(prompt, text: text)
                .submitLabel(.search)
                .autocorrectionDisabled()
                .onSubmit { Task { await model.search(text.wrappedValue, into: role) } }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 11)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 10))
    }

    /// Fastest-to-scenic slider. Re-routes only when the user lets go, so we
    /// don't hammer the backend mid-drag.
    private var prefSlider: some View {
        VStack(spacing: 2) {
            HStack {
                Text("Fastest").font(.caption2)
                Slider(value: $model.pref, in: 0...1) { editing in
                    if !editing { Task { await model.computeRoute() } }
                }
                Text("Scenic").font(.caption2)
            }
            Text("scenery preference \(model.pref, format: .number.precision(.fractionLength(2)))")
                .font(.caption2).foregroundStyle(.secondary)
        }
    }
}

// MARK: - Results

/// The fastest/scenic summary cards, the delta sentence, and the scenery bars.
struct RouteResults: View {
    let response: RouteResponse

    var body: some View {
        let fastest = response.fastest.properties
        let scenic = response.scenic.properties
        let extra = Int((scenic.minutes - fastest.minutes).rounded())
        let maxKm = max(1, scenic.sceneryBreakdown.map(\.km).max() ?? 1)

        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                card("Fastest", fastest, tint: .gray)
                card("Scenic", scenic, tint: .scenic)
            }
            Text(deltaText(extra: extra, from: fastest.mean_score, to: scenic.mean_score))
                .font(.caption)
            ForEach(scenic.sceneryBreakdown, id: \.label) { item in
                SceneryBar(label: item.label, km: item.km, maxKm: maxKm)
            }
        }
    }

    /// One route summary card: big minutes, distance and score beneath.
    private func card(_ title: String, _ p: RouteProps, tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title.uppercased()).font(.caption2).foregroundStyle(.secondary)
            Text("\(Int(p.minutes.rounded())) min").font(.title3.bold())
            Text("\(Int(p.km)) km · \(p.mean_score, format: .number.precision(.fractionLength(1)))/10")
                .font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
    }

    /// "Scenic adds **N min** and raises scenery **x** → **y**" (markdown bold).
    private func deltaText(extra: Int, from: Double, to: Double) -> AttributedString {
        let markdown = "Scenic adds **\(extra) min** and raises scenery "
            + "**\(String(format: "%.1f", from))** → **\(String(format: "%.1f", to))**"
        return (try? AttributedString(markdown: markdown)) ?? AttributedString(markdown)
    }
}

/// One labeled bar in the scenery breakdown ("forest  36 km"), filled in
/// proportion to the longest feature so lengths are easy to compare.
struct SceneryBar: View {
    let label: String
    let km: Double
    let maxKm: Double

    var body: some View {
        HStack(spacing: 8) {
            Text(label).font(.caption2).foregroundStyle(.secondary)
                .frame(width: 74, alignment: .leading)
            GeometryReader { geo in
                Capsule().fill(Color.scenic)
                    .frame(width: geo.size.width * (km / maxKm), height: 6)
                    .frame(maxHeight: .infinity, alignment: .center)
            }
            .frame(height: 10)
            Text("\(Int(km)) km").font(.caption2).frame(width: 40, alignment: .trailing)
        }
    }
}
