import SwiftUI

/// The fastest/scenic summary cards, the delta sentence, and the scenery bars
/// shown in the bottom sheet once a route is computed.
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
            Text("\(Int(p.km.milesFromKm)) mi · \(p.mean_score, format: .number.precision(.fractionLength(1)))/10")
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

/// One labeled bar in the scenery breakdown ("forest  22 mi"), filled in
/// proportion to the longest feature so lengths are easy to compare.
struct SceneryBar: View {
    let label: String
    let km: Double
    let maxKm: Double

    // The label and value columns line the bars up, but a width in fixed points
    // clips its own text as soon as the user raises the system text size.
    // @ScaledMetric grows them with it, so the column survives and the bars stay
    // aligned.
    @ScaledMetric(relativeTo: .caption2) private var labelWidth: CGFloat = 74
    @ScaledMetric(relativeTo: .caption2) private var valueWidth: CGFloat = 40

    var body: some View {
        HStack(spacing: 8) {
            Text(label).font(.caption2).foregroundStyle(.secondary)
                .frame(width: labelWidth, alignment: .leading)
            GeometryReader { geo in
                Capsule().fill(Color.scenic)
                    .frame(width: geo.size.width * (km / maxKm), height: 6)
                    .frame(maxHeight: .infinity, alignment: .center)
            }
            .frame(height: 10)
            Text("\(Int(km.milesFromKm)) mi").font(.caption2)
                .frame(width: valueWidth, alignment: .trailing)
        }
        // Read as one fact. Left to itself VoiceOver announces the label, then a
        // decorative bar, then the number, as three separate stops.
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(label), \(Int(km.milesFromKm)) miles")
    }
}
