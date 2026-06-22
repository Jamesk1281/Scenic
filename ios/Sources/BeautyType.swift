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

    var id: String { apiName }

    /// The six tunable types, in the order they appear on the tune screen.
    /// Keep this list in sync with BEAUTY_TYPES in pipeline/router.py — the
    /// `apiName`s must match the server's, or its weights are ignored.
    static let all: [BeautyType] = [
        BeautyType(apiName: "coast",  label: "Coast"),
        BeautyType(apiName: "forest", label: "Forest & parks"),
        BeautyType(apiName: "town",   label: "Town centers"),
        BeautyType(apiName: "water",  label: "Lakes & rivers"),
        BeautyType(apiName: "hills",  label: "Hills"),
        BeautyType(apiName: "farm",   label: "Farmland"),
    ]

    /// Neutral weight — what every type sits at until the user tunes it.
    static let neutralWeight = 1.0

    /// The slider's range. The midpoint is `neutralWeight`, so "centered" reads
    /// as "no preference"; left ignores the type, right leans into it.
    static let weightRange = 0.0...2.0
}
