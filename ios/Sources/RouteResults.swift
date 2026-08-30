import SwiftUI

/// The fastest/scenic summary cards, the delta sentence, and the scenery bars
/// shown in the bottom sheet once a route is computed.
struct RouteResults: View {
    let response: RouteResponse

    var body: some View {
        let fastest = response.fastest.properties
        let scenic = response.scenic.properties
        let comparison = RouteComparison(fastest: fastest, scenic: scenic)
        let maxKm = max(1, scenic.sceneryBreakdown.map(\.km).max() ?? 1)

        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                card("Fastest", minutes: comparison.fastestMinutes, fastest, tint: .gray)
                card("Scenic", minutes: comparison.scenicMinutes, scenic, tint: .scenic)
            }
            Text(comparison.attributedSummary)
                .font(.caption)
            ForEach(scenic.sceneryBreakdown, id: \.label) { item in
                SceneryBar(label: item.label, km: item.km, maxKm: maxKm)
            }
        }
    }

    /// One route summary card: big minutes, distance and score beneath.
    ///
    /// The minutes are handed in already rounded rather than rounded here, so
    /// the card and the sentence under it are reading the same number — see
    /// `RouteComparison`.
    private func card(_ title: String, minutes: Int, _ p: RouteProps, tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title.uppercased()).font(.caption2).foregroundStyle(.secondary)
            Text("\(minutes) min").font(.title3.bold())
            Text("\(p.km.wholeMilesFromKm) mi · \(p.mean_score, format: .number.precision(.fractionLength(1)))/10")
                .font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
    }
}

/// The arithmetic behind the two summary cards and the sentence beneath them.
///
/// It exists so the two cannot disagree, which they did: each card rounded its
/// own minutes while the delta was rounded from the raw values, so 57.6 and
/// 106.4 rendered as "58 min", "106 min" and "Scenic adds 49 min" — three
/// individually correct roundings that cannot all be true at once, over a
/// subtraction the reader can do in their head. Everything below is derived
/// from the same two rounded integers, so the sentence is arithmetic that
/// checks out against what is on screen.
struct RouteComparison {
    let fastestMinutes: Int
    let scenicMinutes: Int
    let fastestScore: Double
    let scenicScore: Double

    init(fastest: RouteProps, scenic: RouteProps) {
        fastestMinutes = Int(fastest.minutes.rounded())
        scenicMinutes = Int(scenic.minutes.rounded())
        fastestScore = fastest.mean_score
        scenicScore = scenic.mean_score
    }

    /// What the scenic route costs, in the minutes the cards are showing.
    var extraMinutes: Int { scenicMinutes - fastestMinutes }

    /// The two scores exactly as the cards print them. Everything below reads
    /// these rather than the raw doubles, for this type's whole reason to
    /// exist: the sentence has to check out against what is on screen.
    var printedFastestScore: String { String(format: "%.1f", fastestScore) }
    var printedScenicScore: String { String(format: "%.1f", scenicScore) }

    /// Whether the scenery number moves at all, at the precision it is shown to.
    ///
    /// The printed strings, not a tolerance on the raw values — which is what
    /// `abs(difference) < 0.05` was reaching for and missed in both directions.
    /// 4.851 and 4.949 are 0.098 apart and both print "4.9", so the old test
    /// called them different and the sentence claimed a rise the cards
    /// contradicted; 4.949 and 4.951 are 0.002 apart and print "4.9" and "5.0",
    /// so it called them the same while the cards visibly disagreed. Comparing
    /// what is printed cannot be wrong about what is printed.
    var scoreMoves: Bool { printedFastestScore != printedScenicScore }

    /// Whether the two routes read as the same drive. At `pref` 0 the server
    /// answers with the same route twice, and two routes both labelled 4.4 have
    /// nothing to say to each other about scenery whatever their raw scores are.
    var isSameDrive: Bool { extraMinutes <= 0 && !scoreMoves }

    /// The sentence under the cards, as markdown.
    ///
    /// Four shapes past "same drive". "Scenic adds 0 min and raises scenery
    /// 4.4 → 4.4" is a sentence about nothing; a scenic route that costs no
    /// extra time is the best news this screen ever has to deliver and must not
    /// be phrased as a charge of zero; a route that costs time and moves the
    /// number nowhere should say so rather than claim a rise; and the scenic
    /// route can come back scoring *below* the fastest one, which "raises"
    /// reported as an improvement.
    ///
    /// The server no longer returns that last case — see
    /// `_no_worse_than_fastest` in server/app.py, which hands back the fastest
    /// route instead — but this must not depend on that. An app in the store
    /// talks to whichever backend is deployed, including an older one.
    var summary: String {
        let scores = "**\(printedFastestScore)** → **\(printedScenicScore)**"
        if isSameDrive {
            return "**Same as the fastest route** at this setting."
        }
        // Past `isSameDrive`, a score that has not moved implies added minutes.
        if !scoreMoves {
            return "Scenic adds **\(extraMinutes) min** and leaves scenery "
                + "at **\(printedScenicScore)**"
        }
        let verb = scenicScore > fastestScore ? "raises" : "**lowers**"
        if extraMinutes <= 0 {
            return "Scenic \(verb) scenery \(scores) **at no extra time**"
        }
        return "Scenic adds **\(extraMinutes) min** and \(verb) scenery \(scores)"
    }

    /// `summary` with its markdown bold resolved, for display.
    var attributedSummary: AttributedString {
        (try? AttributedString(markdown: summary)) ?? AttributedString(summary)
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
            Text("\(km.wholeMilesFromKm) mi").font(.caption2)
                .frame(width: valueWidth, alignment: .trailing)
        }
        // Read as one fact. Left to itself VoiceOver announces the label, then a
        // decorative bar, then the number, as three separate stops.
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(label), \(km.wholeMilesFromKm) miles")
    }
}
