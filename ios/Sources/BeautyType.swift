import Foundation

/// One user-tunable scenery type, mirroring the server's `BEAUTY_TYPES`
/// (pipeline/router.py).
///
/// `apiName` is the key the backend expects — it's sent as the query parameter
/// `w_<apiName>` (e.g. `w_coast`). `label` is the friendly name shown on the
/// tune screen. A weight of 1.0 is neutral (the calibrated default); higher
/// leans into that type, 0 ignores it.
struct BeautyType: Identifiable {
    let apiName: String
    let label: String

    /// Where this type's slider starts before the user touches it.
    ///
    /// Usually `neutralWeight` — the calibrated blend, which is what "no
    /// preference" means. `town` is the exception and the reason this is a
    /// per-type value rather than one constant; see `note`.
    var defaultWeight: Double = BeautyType.neutralWeight

    /// Why this type does not start where the others do, shown under its
    /// slider. Nil for the types that start neutral, which need no explaining.
    var note: String?

    var id: String { apiName }

    /// The six tunable types, in the order they appear on the tune screen.
    /// Keep this list in sync with BEAUTY_TYPES in pipeline/router.py — the
    /// `apiName`s must match the server's, or its weights are ignored.
    static let all: [BeautyType] = [
        BeautyType(apiName: "coast",  label: "Coast"),
        BeautyType(apiName: "forest", label: "Forest & parks"),
        // Off by default, alone among the six. `c_urban` is the one component
        // the 2026-08-25 drive marks scored *below* chance — 0.35 separation
        // against a 0.50 coin, the only one of nine pointing the wrong way —
        // while carrying weight 0.14 in score.py's blend. Measured on the
        // Massachusetts graph, sending w_town=0 costs nothing to buy back:
        // Needham→Wachusett at pref 1.0 sheds 12.1 km of built-up road, gains
        // 5.0 km of forest, and arrives 5.1 minutes *earlier*;
        // Boston→Northampton at pref 0.5 sheds 12.4 km and is 1.0 min faster.
        // Coastal trips are unaffected (Needham→Rockport: no change at all),
        // because there the towns are on the coast and the coast is what buys
        // them.
        //
        // Left tunable rather than deleted: the component conflates a village
        // green with a retail park (score.py gives both 1.0), so the signal is
        // miscast rather than worthless, and a driver who wants town centres
        // can still ask for them.
        BeautyType(apiName: "town",   label: "Town centers",
                   defaultWeight: 0.0,
                   note: "Off by default — drivers rated built-up stretches "
                       + "worse than the score predicted."),
        BeautyType(apiName: "water",  label: "Lakes & rivers"),
        BeautyType(apiName: "hills",  label: "Hills"),
        BeautyType(apiName: "farm",   label: "Farmland"),
    ]

    /// Neutral weight — the calibrated blend, and what the middle of the slider
    /// means. Most types start here; see `defaultWeight` for the one that
    /// doesn't.
    static let neutralWeight = 1.0

    /// The slider's range. The midpoint is `neutralWeight`, so "centered" reads
    /// as "no preference"; left ignores the type, right leans into it.
    static let weightRange = 0.0...2.0
}
