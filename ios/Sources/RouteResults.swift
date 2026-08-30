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
                card("Fastest", minutes: comparison.fastestMinutes,
                     km: fastest.km, detail: comparison.fastestDetail, tint: .gray)
                card("Scenic", minutes: comparison.scenicMinutes,
                     km: scenic.km, detail: comparison.scenicDetail, tint: .scenic)
            }
            Text(comparison.attributedSummary)
                .font(.caption)
            ForEach(scenic.sceneryBreakdown, id: \.label) { item in
                SceneryBar(label: item.label, km: item.km, maxKm: maxKm)
            }
        }
    }

    /// One route summary card: big minutes, distance and beautiful miles
    /// beneath.
    ///
    /// The minutes and the `detail` half are both handed in already formatted
    /// rather than formatted here, so the card and the sentence under it are
    /// reading the same numbers — see `RouteComparison`.
    private func card(_ title: String, minutes: Int, km: Double,
                      detail: String, tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title.uppercased()).font(.caption2).foregroundStyle(.secondary)
            Text("\(minutes) min").font(.title3.bold())
            Text("\(km.wholeMilesFromKm) mi · \(detail)")
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
    /// Whole miles of road scoring 7+ on each arm, rounded once here so the
    /// cards and the sentence cannot print different integers — nil when the
    /// backend predates `beautiful_km`. See `RouteProps.beautiful_km`.
    let fastestBeautifulMiles: Int?
    let scenicBeautifulMiles: Int?

    init(fastest: RouteProps, scenic: RouteProps) {
        fastestMinutes = Int(fastest.minutes.rounded())
        scenicMinutes = Int(scenic.minutes.rounded())
        fastestScore = fastest.mean_score
        scenicScore = scenic.mean_score
        fastestBeautifulMiles = fastest.beautiful_km?.wholeMilesFromKm
        scenicBeautifulMiles = scenic.beautiful_km?.wholeMilesFromKm
    }

    /// What the scenic route costs, in the minutes the cards are showing.
    var extraMinutes: Int { scenicMinutes - fastestMinutes }

    /// The two mile counts, but only when *both* arms carry one.
    ///
    /// Taken as a pair rather than one at a time so the screen can never end up
    /// with a mile count on one card and a 0–10 score on the other. In practice
    /// both arms come from one response and one backend, so this is nil or
    /// whole; the pair makes that structural rather than assumed.
    var beautifulMiles: (fastest: Int, scenic: Int)? {
        guard let fastest = fastestBeautifulMiles, let scenic = scenicBeautifulMiles
        else { return nil }
        return (fastest, scenic)
    }

    /// The two scores as the cards used to print them, and still the ruler
    /// `scoreMoves` and the fallback sentence measure with.
    var printedFastestScore: String { String(format: "%.1f", fastestScore) }
    var printedScenicScore: String { String(format: "%.1f", scenicScore) }

    /// What each card prints to the right of its distance: beautiful miles
    /// where the backend reports them, the 0–10 mean where it does not. The
    /// sentence below is built from the same values, for this type's whole
    /// reason to exist — it has to check out against what is on screen.
    var fastestDetail: String {
        beautifulMiles.map { "\($0.fastest) mi beautiful" } ?? "\(printedFastestScore)/10"
    }
    var scenicDetail: String {
        beautifulMiles.map { "\($0.scenic) mi beautiful" } ?? "\(printedScenicScore)/10"
    }

    /// Whether the mean score moves at all, at the precision it *would* be
    /// shown to.
    ///
    /// The printed strings, not a tolerance on the raw values — which is what
    /// `abs(difference) < 0.05` was reaching for and missed in both directions.
    /// 4.851 and 4.949 are 0.098 apart and both print "4.9", so the old test
    /// called them different and the sentence claimed a rise the cards
    /// contradicted; 4.949 and 4.951 are 0.002 apart and print "4.9" and "5.0",
    /// so it called them the same while the cards visibly disagreed.
    ///
    /// Since the cards moved to miles this no longer describes what is on
    /// screen — it rounds a number the driver is not shown. That is deliberate
    /// and it is the *only* thing left reading the score: `isSameDrive` is
    /// defined on it, and that definition is measured (see below), so rebuilding
    /// this on the mile counts would silently throw the measurement away. It
    /// stays a question about `mean_score` at one decimal place, which is a
    /// well-defined question whether or not the answer is printed.
    var scoreMoves: Bool { printedFastestScore != printedScenicScore }

    /// Whether the printed mile counts differ — the on-screen question the
    /// sentence is actually built from. Compares the rounded integers the cards
    /// show, so "turns 3 mi into 4 mi" can never appear over two cards reading
    /// the same number.
    var beautifulMilesMove: Bool {
        guard let miles = beautifulMiles else { return false }
        return miles.fastest != miles.scenic
    }

    /// Whether the two routes read as the same drive. At `pref` 0 the server
    /// answers with the same route twice, and two routes both labelled 4.4 have
    /// nothing to say to each other about scenery whatever their raw scores are.
    ///
    /// Still `mean_score`-based after the cards moved to miles, on purpose. The
    /// 983-pair census replayed *this exact definition*: it fires on 11.5% of
    /// trips, catches 105 of the 106 where the scenic arm genuinely is the
    /// fastest arm, and across all 113 it fires on the largest gain is **0.22
    /// beautiful miles** — which rounds to the same whole mile on both cards
    /// anyway, so it never hides a difference the driver could have seen.
    /// Redefining it on the mile counts would need a new census.
    var isSameDrive: Bool { extraMinutes <= 0 && !scoreMoves }

    /// The sentence under the cards, as markdown.
    ///
    /// Four shapes past "same drive". "Scenic adds 0 min and turns 3 mi of
    /// beautiful road into 3 mi" is a sentence about nothing; a scenic route
    /// that costs no extra time is the best news this screen ever has to
    /// deliver and must not be phrased as a charge of zero; a route that costs
    /// time and moves the number nowhere should say so rather than claim a
    /// gain; and the scenic route can come back with *less* beautiful road than
    /// the fastest one.
    ///
    /// That last case is the point of counting miles rather than averaging a
    /// score. On 3.1% of 983 sampled trips the scenic arm is slower and has
    /// less beautiful road, and `mean_score` *rose* on 27 of those 30 — a
    /// shorter route with a better per-kilometre average genuinely scores
    /// higher — so the old sentence read "adds 2 min and raises scenery 4.1 →
    /// 4.7" about a trip where the good road went down. `_no_worse_than_fastest`
    /// in `server/app.py` now catches the six that were worse on the score too;
    /// the other 24 still ship and the sentence has to describe them honestly.
    /// So the fall is a plain statement, not clamped to zero and not an error.
    ///
    /// Falls back to the 0–10 sentence when the backend did not send
    /// `beautiful_km`, which is the same reasoning as `_no_worse_than_fastest`
    /// above: an app in the store talks to whichever backend is deployed,
    /// including an older one.
    var summary: String {
        if isSameDrive {
            return "**Same as the fastest route** at this setting."
        }
        guard let miles = beautifulMiles else { return scoreSummary }

        let from = "**\(miles.fastest) mi**", to = "**\(miles.scenic) mi**"
        if !beautifulMilesMove {
            // Past `isSameDrive` this is either time spent for no more good
            // road, or — when the mean moved but the whole miles did not — a
            // genuinely different route that happens to tie on the count.
            return extraMinutes > 0
                ? "Scenic adds **\(extraMinutes) min** and leaves beautiful road at \(to)"
                : "A different route with the same \(to) of beautiful road, at no extra time"
        }
        // "turns A into B" reads in both directions, and the fall gets a word
        // of its own so it cannot be skimmed past as a gain.
        let change = miles.scenic > miles.fastest
            ? "turns \(from) of beautiful road into \(to)"
            : "**cuts** beautiful road from \(from) to \(to)"
        if extraMinutes <= 0 {
            return "Scenic \(change) **at no extra time**"
        }
        return "Scenic adds **\(extraMinutes) min** and \(change)"
    }

    /// The sentence as it shipped before the cards counted miles, kept whole
    /// for backends that predate `beautiful_km`. Reached only through
    /// `summary`, and past its `isSameDrive` check.
    var scoreSummary: String {
        let scores = "**\(printedFastestScore)** → **\(printedScenicScore)**"
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
