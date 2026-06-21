import MapKit
import SwiftUI

/// The app's accent green, used for the scenic route line and highlights.
/// Defined once here so every view refers to the same color.
extension Color {
    static let scenic = Color(red: 0.22, green: 0.83, blue: 0.62)
}

// MARK: - Scenery breakdown bar

/// One labeled bar in the "scenic route passes" breakdown, e.g. "forest  36 km".
/// The bar fills proportionally to the longest feature so the lengths are easy
/// to compare at a glance.
struct SceneryBar: View {
    let label: String
    let km: Double
    /// The largest km value across all bars, used to scale this bar's width.
    let maxKm: Double

    var body: some View {
        HStack(spacing: 8) {
            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .frame(width: 74, alignment: .leading)

            // GeometryReader gives us the available width so we can size the
            // filled portion as a fraction of it.
            GeometryReader { geo in
                Capsule()
                    .fill(Color.scenic)
                    .frame(width: geo.size.width * (km / maxKm), height: 6)
                    .frame(maxHeight: .infinity, alignment: .center)
            }
            .frame(height: 10)

            Text("\(Int(km.rounded())) km")
                .font(.caption2)
                .frame(width: 44, alignment: .trailing)
        }
    }
}

// MARK: - Main screen

struct ContentView: View {
    /// All trip state lives in the model; this view just reflects it.
    @State private var model = RouteModel()
    /// Talks to Core Location for the "use my location" button.
    @State private var locationManager = LocationManager()

    /// The map camera. Starts framed on Massachusetts; we move it when the user
    /// picks a location or a route comes back.
    @State private var camera: MapCameraPosition = .region(
        MKCoordinateRegion(
            center: CLLocationCoordinate2D(latitude: 42.15, longitude: -71.8),
            span: MKCoordinateSpan(latitudeDelta: 2.6, longitudeDelta: 2.6)
        )
    )

    var body: some View {
        // GeometryReader gives us the screen size so the bottom panel can cap
        // its height to a fraction of it — keeping the map visible on small
        // phones and in landscape.
        GeometryReader { geo in
            ZStack(alignment: .bottom) {
                mapLayer
                bottomPanel(maxHeight: geo.size.height * 0.62)
            }
        }
    }

    // MARK: Map

    private var mapLayer: some View {
        // MapReader lets us convert a tap point into a map coordinate.
        MapReader { proxy in
            Map(position: $camera) {
                routeOverlays
                endpointMarkers
            }
            .mapStyle(.standard(elevation: .realistic))
            // Tap the map to drop a start, then a destination.
            .gesture(
                SpatialTapGesture().onEnded { tap in
                    if let coord = proxy.convert(tap.location, from: .local) {
                        model.handleTap(coord)
                    }
                }
            )
            // When a new scenic route arrives, frame the whole thing.
            .onChange(of: model.response?.scenic.coordinates.count) {
                frameRoute()
            }
        }
        .ignoresSafeArea(edges: .top)
    }

    /// The two route lines: fastest (gray, dashed) drawn under scenic (green).
    @MapContentBuilder
    private var routeOverlays: some MapContent {
        if let fastest = model.response?.fastest {
            MapPolyline(coordinates: fastest.coordinates)
                .stroke(.gray, style: StrokeStyle(lineWidth: 4, dash: [6, 5]))
        }
        if let scenic = model.response?.scenic {
            MapPolyline(coordinates: scenic.coordinates)
                .stroke(Color.scenic, lineWidth: 6)
        }
    }

    /// Start (green) and end (red) pins.
    @MapContentBuilder
    private var endpointMarkers: some MapContent {
        if let start = model.start {
            Annotation("Start", coordinate: start) { pin(.green) }
        }
        if let end = model.end {
            Annotation("End", coordinate: end) { pin(.red) }
        }
    }

    /// A simple colored map pin.
    private func pin(_ color: Color) -> some View {
        Circle()
            .fill(color)
            .stroke(.white, lineWidth: 2)
            .frame(width: 16, height: 16)
            .shadow(radius: 2)
    }

    // MARK: Bottom control panel

    /// The frosted card at the bottom. It sizes to its content when short (no
    /// route yet) and becomes scrollable, capped at `maxHeight`, once a route
    /// fills it with cards and bars — so it never pushes off-screen.
    private func bottomPanel(maxHeight: CGFloat) -> some View {
        // ViewThatFits tries the first child and uses it if it fits the
        // available height; otherwise it falls through to the next. So the
        // panel sizes to its content (no dead space) when everything fits, and
        // only becomes a capped, scrollable view when the content is genuinely
        // too tall for the screen (e.g. a full result on a small phone).
        ViewThatFits(in: .vertical) {
            panelContent
            ScrollView { panelContent }
        }
        .frame(maxHeight: maxHeight)
        .padding(14)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 24))
        .padding(.horizontal, 12)
        .padding(.bottom, 8)
    }

    /// Everything inside the panel: title, address fields, slider, and results.
    private var panelContent: some View {
        VStack(spacing: 11) {
            header
            addressFields
            preferenceSlider

            if model.isLoading {
                ProgressView()
            }
            if let error = model.errorText {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.red)
            }
            if let response = model.response {
                comparison(response)
            }
        }
    }

    /// Title + status line on the left, swap/clear buttons on the right.
    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 1) {
                Text("Scenic").font(.title2.bold())
                Text(statusLine)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if model.start != nil {
                Button { model.swapEnds() } label: {
                    Image(systemName: "arrow.left.arrow.right")
                }
                .disabled(model.end == nil)

                Button { model.clear() } label: {
                    Image(systemName: "xmark.circle")
                }
            }
        }
    }

    /// A short hint that reflects where the user is in the flow.
    private var statusLine: String {
        if model.response != nil { return "Tap the map to start over" }
        if model.start != nil { return "Set a destination" }
        return "Search or tap the map to begin"
    }

    /// The two address inputs. The start field also offers a "use my location"
    /// button so a trip can begin from the device's current position.
    private var addressFields: some View {
        VStack(spacing: 8) {
            addressField(
                "Start address or place",
                text: $model.startQuery,
                dot: .green,
                role: .start,
                showLocationButton: true
            )
            addressField(
                "Destination address or place",
                text: $model.endQuery,
                dot: .red,
                role: .end,
                showLocationButton: false
            )
            if locationManager.accessDenied {
                Text("Location access is off — enable it in Settings, or type a start address.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    /// One address input row: a colored dot, a text field that searches on
    /// submit, and (optionally) a location button.
    private func addressField(
        _ prompt: String,
        text: Binding<String>,
        dot: Color,
        role: Endpoint,
        showLocationButton: Bool
    ) -> some View {
        HStack(spacing: 8) {
            Circle().fill(dot).frame(width: 9, height: 9)

            TextField(prompt, text: text)
                .textFieldStyle(.plain)
                .submitLabel(.search)
                .autocorrectionDisabled()
                .onSubmit {
                    Task { await model.search(text.wrappedValue, into: role) }
                }

            if showLocationButton {
                Button(action: useCurrentLocation) {
                    Image(systemName: "location.fill")
                        .foregroundStyle(Color.scenic)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Use my current location")
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 10))
    }

    /// The fastest-to-scenic preference slider. We re-route only when the user
    /// lets go (the `editing` flag is false), so we don't spam the backend
    /// while they're still dragging.
    private var preferenceSlider: some View {
        VStack(spacing: 2) {
            HStack {
                Text("Fastest").font(.caption2)
                Slider(value: $model.pref, in: 0...1) { editing in
                    if !editing {
                        Task { await model.computeRoute() }
                    }
                }
                Text("Scenic").font(.caption2)
            }
            Text("scenery preference \(model.pref, format: .number.precision(.fractionLength(2)))")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }

    // MARK: Results

    /// The side-by-side fastest/scenic cards, the summary sentence, and the
    /// per-feature distance breakdown for the scenic route.
    private func comparison(_ response: RouteResponse) -> some View {
        let fastest = response.fastest.properties
        let scenic = response.scenic.properties
        let extraMinutes = Int((scenic.minutes - fastest.minutes).rounded())
        // Longest feature distance, used to scale every bar's fill width.
        let maxKm = max(1, scenic.sceneryBreakdown.map(\.km).max() ?? 1)

        return VStack(spacing: 8) {
            HStack(spacing: 10) {
                routeCard("Fastest", fastest, tint: .gray)
                routeCard("Scenic", scenic, tint: .scenic)
            }

            Text(deltaText(extraMinutes: extraMinutes,
                           from: fastest.mean_score,
                           to: scenic.mean_score))
                .font(.caption)
                .frame(maxWidth: .infinity, alignment: .leading)

            ForEach(scenic.sceneryBreakdown, id: \.label) { item in
                SceneryBar(label: item.label, km: item.km, maxKm: maxKm)
            }
        }
    }

    /// "Scenic adds **N min** and raises scenery **x** → **y**", with the bold
    /// markdown rendered via AttributedString.
    private func deltaText(extraMinutes: Int, from: Double, to: Double) -> AttributedString {
        let markdown = "Scenic adds **\(extraMinutes) min** and raises scenery "
            + "**\(String(format: "%.1f", from))** → **\(String(format: "%.1f", to))**"
        return (try? AttributedString(markdown: markdown)) ?? AttributedString(markdown)
    }

    /// One route summary card: minutes big, distance and score beneath.
    private func routeCard(_ title: String, _ props: RouteProps, tint: Color) -> some View {
        let minutes = Int(props.minutes.rounded())
        let detail = "\(String(format: "%.0f", props.km)) km · "
            + "\(String(format: "%.1f", props.mean_score))/10"

        return VStack(alignment: .leading, spacing: 3) {
            Text(title.uppercased())
                .font(.caption2)
                .foregroundStyle(.secondary)
            Text("\(minutes) min").font(.title3.bold())
            Text(detail)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .strokeBorder(tint.opacity(0.5), lineWidth: 1)
        )
    }

    // MARK: Actions

    /// Ask Core Location for the current position and use it as the start.
    private func useCurrentLocation() {
        locationManager.requestCurrentLocation { coord in
            model.setStart(coord, label: "Current location")
            withAnimation {
                camera = .region(
                    MKCoordinateRegion(
                        center: coord,
                        span: MKCoordinateSpan(latitudeDelta: 0.2, longitudeDelta: 0.2)
                    )
                )
            }
        }
    }

    /// Zoom and pan the camera so the whole scenic route fits *in the map area
    /// above the control panel* — not just centered on screen.
    private func frameRoute() {
        guard let coords = model.response?.scenic.coordinates, !coords.isEmpty else { return }

        // Build the smallest map rectangle that contains every route point.
        var bounds = MKMapRect.null
        for coord in coords {
            let point = MKMapPoint(coord)
            bounds = bounds.union(MKMapRect(x: point.x, y: point.y, width: 0, height: 0))
        }

        // The panel covers the bottom of the screen, so simply centering the
        // route would tuck it behind the panel. Instead we pad the bounding
        // rect asymmetrically — a little on the sides and top, a lot on the
        // bottom. MapKit centers the *padded* rect, and that big bottom margin
        // pushes the actual route up into the visible map area above the panel.
        // (MKMapPoint y grows southward, so adding height extends the rect south.)
        let w = bounds.size.width
        let h = bounds.size.height
        let sidePad = w * 0.15
        let topPad = h * 0.25
        // The on-screen zoom is driven by whichever dimension is larger relative
        // to the (tall, portrait) screen — usually the width for an east-west
        // route. Base the bottom margin on the larger dimension so even a very
        // wide route gets lifted clear of the panel, not just a tall one.
        let bottomPad = max(h * 1.6, w * 1.1)

        let framed = MKMapRect(
            x: bounds.origin.x - sidePad,
            y: bounds.origin.y - topPad,
            width: w + sidePad * 2,
            height: h + topPad + bottomPad
        )
        withAnimation {
            camera = .rect(framed)
        }
    }
}
