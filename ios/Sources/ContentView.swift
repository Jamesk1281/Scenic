import MapKit
import SwiftUI

struct SceneryBar: View {
    let label: String
    let km: Double
    let maxKm: Double

    var body: some View {
        HStack(spacing: 8) {
            Text(label).font(.caption2).foregroundStyle(.secondary)
                .frame(width: 74, alignment: .leading)
            GeometryReader { geo in
                Capsule()
                    .fill(ContentView.scenicGreen)
                    .frame(width: geo.size.width * (km / maxKm), height: 6)
                    .frame(maxHeight: .infinity, alignment: .center)
            }
            .frame(height: 10)
            Text("\(Int(km.rounded())) km")
                .font(.caption2).frame(width: 44, alignment: .trailing)
        }
    }
}

struct ContentView: View {
    @State private var model = RouteModel()
    @State private var camera: MapCameraPosition = .region(
        MKCoordinateRegion(
            center: CLLocationCoordinate2D(latitude: 42.15, longitude: -71.8),
            span: MKCoordinateSpan(latitudeDelta: 2.6, longitudeDelta: 2.6)
        )
    )

    var body: some View {
        ZStack(alignment: .bottom) {
            MapReader { proxy in
                Map(position: $camera) {
                    if let f = model.response?.fastest {
                        MapPolyline(coordinates: f.coordinates)
                            .stroke(.gray, style: StrokeStyle(lineWidth: 4, dash: [6, 5]))
                    }
                    if let s = model.response?.scenic {
                        MapPolyline(coordinates: s.coordinates)
                            .stroke(Color(red: 0.22, green: 0.83, blue: 0.62), lineWidth: 6)
                    }
                    if let a = model.start {
                        Annotation("Start", coordinate: a) { pin(.green) }
                    }
                    if let b = model.end {
                        Annotation("End", coordinate: b) { pin(.red) }
                    }
                }
                .mapStyle(.standard(elevation: .realistic))
                .gesture(
                    SpatialTapGesture().onEnded { event in
                        if let coord = proxy.convert(event.location, from: .local) {
                            model.handleTap(coord)
                        }
                    }
                )
                .onChange(of: model.response?.scenic.coordinates.count) {
                    fitRoute()
                }
            }
            .ignoresSafeArea(edges: .top)

            controlCard
        }
    }

    private func pin(_ color: Color) -> some View {
        Circle()
            .fill(color)
            .stroke(.white, lineWidth: 2)
            .frame(width: 16, height: 16)
            .shadow(radius: 2)
    }

    private func addressField(
        _ prompt: String, text: Binding<String>, dot: Color, role: Endpoint
    ) -> some View {
        HStack(spacing: 8) {
            Circle().fill(dot).frame(width: 9, height: 9)
            TextField(prompt, text: text)
                .textFieldStyle(.plain)
                .submitLabel(.search)
                .autocorrectionDisabled()
                .onSubmit { Task { await model.search(text.wrappedValue, into: role) } }
        }
        .padding(.horizontal, 12).padding(.vertical, 10)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 10))
    }

    private var controlCard: some View {
        VStack(spacing: 14) {
            HStack {
                VStack(alignment: .leading, spacing: 1) {
                    Text("Scenic").font(.title2.bold())
                    Text(statusLine).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if model.start != nil {
                    Button { model.swapEnds() } label: { Image(systemName: "arrow.left.arrow.right") }
                        .disabled(model.end == nil)
                    Button { model.clear() } label: { Image(systemName: "xmark.circle") }
                }
            }

            VStack(spacing: 8) {
                addressField("Start address or place", text: $model.startQuery,
                             dot: .green, role: .start)
                addressField("Destination address or place", text: $model.endQuery,
                             dot: .red, role: .end)
            }

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

            if model.isLoading { ProgressView() }
            if let err = model.errorText {
                Text(err).font(.caption).foregroundStyle(.red)
            }
            if let r = model.response { comparison(r) }
        }
        .padding(16)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 20))
        .padding(12)
    }

    private var statusLine: String {
        if model.response != nil { return "Tap the map to start over" }
        if model.start != nil { return "Tap a destination" }
        return "Tap the map to set a start"
    }

    static let scenicGreen = Color(red: 0.22, green: 0.83, blue: 0.62)

    private func comparison(_ r: RouteResponse) -> some View {
        let f = r.fastest.properties
        let s = r.scenic.properties
        let extra = Int((s.minutes - f.minutes).rounded())
        let maxKm = max(1, s.sceneryBreakdown.map(\.km).max() ?? 1)
        return VStack(spacing: 10) {
            HStack(spacing: 10) {
                routeCard("Fastest", f, .gray)
                routeCard("Scenic", s, Self.scenicGreen)
            }
            Text(deltaText(extra: extra, from: f.mean_score, to: s.mean_score))
                .font(.caption)
                .frame(maxWidth: .infinity, alignment: .leading)
            ForEach(s.sceneryBreakdown, id: \.label) { item in
                SceneryBar(label: item.label, km: item.km, maxKm: maxKm)
            }
        }
    }

    private func deltaText(extra: Int, from: Double, to: Double) -> AttributedString {
        let md = "Scenic adds **\(extra) min** and raises scenery "
            + "**\(String(format: "%.1f", from))** → **\(String(format: "%.1f", to))**"
        return (try? AttributedString(markdown: md)) ?? AttributedString(md)
    }

    private func routeCard(_ title: String, _ p: RouteProps, _ tint: Color) -> some View {
        let minutes = Int(p.minutes.rounded())
        let detail = "\(String(format: "%.0f", p.km)) km · \(String(format: "%.1f", p.mean_score))/10"
        return VStack(alignment: .leading, spacing: 3) {
            Text(title.uppercased()).font(.caption2).foregroundStyle(.secondary)
            Text("\(minutes) min").font(.title3.bold())
            Text(detail).font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(tint.opacity(0.5), lineWidth: 1))
    }

    private func fitRoute() {
        guard let coords = model.response?.scenic.coordinates, !coords.isEmpty else { return }
        var rect = MKMapRect.null
        for c in coords {
            let p = MKMapPoint(c)
            rect = rect.union(MKMapRect(x: p.x, y: p.y, width: 0, height: 0))
        }
        let padded = rect.insetBy(dx: -rect.size.width * 0.15, dy: -rect.size.height * 0.25)
        withAnimation { camera = .rect(padded) }
    }
}
