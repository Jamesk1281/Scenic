import MapKit
import SwiftUI
import UIKit

extension PresentationDetent {
    /// The planning sheet's resting height before the user engages with it:
    /// the two fields and the slider, and not much more.
    static let planningCompact = PresentationDetent.custom(PlanningCompactDetent.self)
}

/// The map between the preference slider's handle and the `pref` the API takes.
///
/// They are not the same number, and that is the whole point. The router
/// squares the preference — `PREF_CURVE = 2.0` at `pipeline/router.py:152`,
/// applied at `:919` as `strength = clamp(pref) ** PREF_CURVE` — so a handle
/// moving linearly through `pref` moves *quadratically* through the quantity
/// that actually picks the route, and the bottom of the track lands in the flat
/// part of the curve. Measured over 252 pairs at five settings
/// (`docs/route-distribution-study.md`, Q3), against pref 0:
///
///     pref   strength   extra min   beautiful mi   step changes nothing
///     0.25       0.06         0.5           0.20                    33%
///     0.50       0.25        16.5           5.06                    16%
///     0.75       0.56        24.5           7.01                    22%
///     1.00       1.00        28.1           9.20                    28%
///
/// The first quarter of the travel buys 2% of what the whole slider buys, and a
/// third of trips at that setting get back the route they already had.
///
/// Mapping the handle's position `p` to `pref = sqrt(p)` makes `strength == p`,
/// so every quarter of the travel does comparable work. Read off the table, the
/// first quarter then buys about 55% of the available beautiful miles for about
/// 59% of the available minutes — still front-loaded, because the outcome
/// saturates in strength too, but no longer dead. Nothing can make the travel
/// linear in *outcome*: that curve is route-dependent. Linear in strength is
/// the honest fixed transform.
///
/// Two things this must not do.
///
/// It must not become the value anyone stores. `RouteModel.pref` is what
/// `server/app.py` is asked for, what goes into every `DriveTrace` header, what
/// `NavigationModel` reroutes with, and the quantity the census and all twelve
/// recorded drives are indexed by. Only `RoutePanel.prefPosition` holds a
/// position.
///
/// And it must not blur `pref == 0`, which is magic in three places:
/// `server/app.py` short-circuits (`scenic = fastest if pref == 0.0`),
/// `NavigationModel.reroute` reads it as `wantFastest`, and `switchToFastest`
/// writes it. `sqrt(0)` and `0 * 0` are both exactly 0, so the far ends survive
/// the round trip bit-for-bit — but nothing here may round, smooth or animate,
/// or the "Fastest" end of the track quietly stops being the fastest route.
enum PrefSlider {

    /// The `pref` a handle at `position` is asking for. Exact at both ends.
    static func pref(atPosition position: Double) -> Double {
        (position < 0 ? 0 : position > 1 ? 1 : position).squareRoot()
    }

    /// Where the handle sits for a given `pref` — the inverse, and the router's
    /// `strength` for that `pref`.
    static func position(forPref pref: Double) -> Double {
        let p = pref < 0 ? 0 : pref > 1 ? 1 : pref
        return p * p
    }
}

/// How tall the compact planning sheet has to be to hold its own content.
///
/// It used to be a literal `.height(260)`, and the block measures more than
/// that at the default text size — so "scenery preference 0.50" was bisected by
/// the sheet's bottom edge on the first screen of every cold launch, before the
/// user had touched an accessibility setting. Raising the literal would only
/// move the failure one text size along: the title, the hint, the picker, both
/// fields and the slider caption are all typed, so the block grows with Dynamic
/// Type and no constant can follow it.
///
/// A `custom` detent rather than a computed `.height(x)` because a custom
/// detent's *identity is its type*: `RoutePanel` compares `detent ==
/// .planningCompact` in three places and `ContentView` seeds the selection with
/// it, and a height that changed with the text size would silently stop
/// matching. It also has to be this rather than measuring the content and
/// assigning a detent to fit, which is the mechanism `onChange(of: focused)`
/// below documents at length as leaving UIKit holding a stale hit-test frame.
struct PlanningCompactDetent: CustomPresentationDetent {

    /// The two halves of the block's height, in points, split so the padding
    /// isn't scaled along with the text.
    ///
    /// Solved from the block measured on a booted iPhone 15 Pro: **311 pt** at
    /// the default text size and **363 pt** at xxxLarge, where `UIFontMetrics`
    /// reports a body scale of 1.0 and 1.3167. Those are the two ends of the
    /// non-accessibility range, so the formula is exact at both and interpolates
    /// between them (it lands within 8 pt at accessibility-medium, on the
    /// generous side).
    ///
    /// Scaling the whole 311 would overshoot instead: most of it is padding,
    /// gaps and control chrome that don't grow with text at all.
    static let fixedPoints: CGFloat = 147
    static let textPoints: CGFloat = 164

    /// Room under the block for the home indicator, which overlapped the slider
    /// caption even where the caption itself wasn't cut. Slack rather than
    /// clipping on a device that has no home indicator.
    static let bottomInset: CGFloat = 34

    /// The most of the screen this detent will take. A planning sheet that has
    /// eaten the whole map is not a trade anyone chose.
    static let maximumFraction: CGFloat = 0.85

    static func height(in context: Context) -> CGFloat? {
        let cap = context.maxDetentValue * maximumFraction

        // At the accessibility sizes there is no honest answer: the same block
        // measures 884 pt at AX5, which is taller than an iPhone 15 Pro's whole
        // 852 pt screen, so no detent can show all of it. Take everything the
        // sheet is allowed and let the `ScrollView` carry the rest — which also
        // means the compact detent is taller than `.medium` there, and
        // `onChange(of: focused)` below lowers rather than raises it. That is
        // the right way round while a keyboard is up.
        guard !context.dynamicTypeSize.isAccessibilitySize else { return cap }

        let traits = UITraitCollection(
            preferredContentSizeCategory: contentSizeCategory(context.dynamicTypeSize))
        let scaled = UIFontMetrics(forTextStyle: .body)
            .scaledValue(for: textPoints, compatibleWith: traits)
        return min(fixedPoints + scaled + bottomInset, cap)
    }

    /// SwiftUI's `DynamicTypeSize` and UIKit's `UIContentSizeCategory` are the
    /// same twelve steps under two names. A detent's `Context` offers only the
    /// first; `UIFontMetrics` takes only the second.
    private static func contentSizeCategory(_ size: DynamicTypeSize) -> UIContentSizeCategory {
        switch size {
        case .xSmall:  return .extraSmall
        case .small:   return .small
        case .medium:  return .medium
        case .large:   return .large
        case .xLarge:  return .extraLarge
        case .xxLarge: return .extraExtraLarge
        case .xxxLarge: return .extraExtraExtraLarge
        case .accessibility1: return .accessibilityMedium
        case .accessibility2: return .accessibilityLarge
        case .accessibility3: return .accessibilityExtraLarge
        case .accessibility4: return .accessibilityExtraExtraLarge
        case .accessibility5: return .accessibilityExtraExtraExtraLarge
        @unknown default: return .large
        }
    }
}

/// The bottom-sheet panel: two address searches, the preference slider, the
/// route comparison once both ends are set, and a button to start driving.
struct RoutePanel: View {
    @Bindable var model: RouteModel

    /// How tall the sheet is. Owned by `ContentView` (which presents the sheet)
    /// but driven from here, where we know what the user is doing.
    @Binding var detent: PresentationDetent

    /// Drives the live autocomplete dropdown.
    @State private var completer = SearchCompleter()
    /// Which field (if any) the user is typing in — so suggestions show under
    /// the right one.
    @FocusState private var focused: Endpoint?
    /// Whether the "Tune scenery" sheet is showing.
    @State private var showingTune = false

    var body: some View {
        // The route comparison scrolls; "Start scenic drive" doesn't. Pinning it
        // outside the ScrollView keeps the primary action on screen at any sheet
        // height, instead of hiding below the fold at the medium detent.
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    header
                    modePicker
                    switch model.mode {
                    case .directions:
                        directionsContent
                    case .loops:
                        LoopPanel(model: model.loops) { response in
                            model.startLoopDrive(response)
                        }
                    }
                }
                .padding(20)
            }

            // Only the directions tab pins its primary action outside the
            // scroll view. The loop tab's two buttons sit with the numbers they
            // act on, and its panel is short enough that they are never below
            // the fold.
            if model.mode == .directions, let response = model.response {
                startButton(for: response)
                    .padding(.horizontal, 20)
                    .padding(.bottom, 10)
            }
        }
        // Once the user is working in the panel, never let it sit at the compact
        // height again. Choosing a suggestion dismisses the keyboard, and the
        // sheet used to drop back to 260pt as the keyboard left — which reads,
        // to anyone using this, as the panel closing itself the instant an
        // address is set. Resting at .medium keeps both fields, the slider and
        // the top of the results in view.
        //
        // Deliberately *raising* rather than assigning: forcing the sheet to a
        // specific detent while the keyboard is animating leaves UIKit with a
        // stale hit-test frame, and taps in the newly exposed top half of the
        // sheet — the first suggestion rows — silently do nothing. Nudging it
        // off the compact detent and letting the keyboard drive the rest avoids
        // that entirely, and leaves a user who dragged to .large where they put
        // themselves.
        .onChange(of: focused) { _, _ in
            if detent == .planningCompact { detent = .medium }
        }
        // A route arriving without a focus change (demo mode, or a slider
        // re-route) should still open the panel up enough to show it.
        .onChange(of: model.response == nil) { _, noRoute in
            if !noRoute, detent == .planningCompact { detent = .medium }
        }
        .sheet(isPresented: $showingTune) {
            TuneView(model: model)
        }
        // The tune screen edits one set of beauty weights for the whole app, so
        // the loop tab has to be routing under them too. Pushed rather than
        // read through a back-reference, which would be a retain cycle.
        .onChange(of: model.weights) { _, weights in model.loops.weights = weights }
        .task { model.loops.weights = model.weights }
        // A loop arriving should open the sheet enough to show it, the same way
        // a route does.
        .onChange(of: model.loops.response == nil) { _, noLoop in
            if !noLoop, detent == .planningCompact { detent = .medium }
        }
    }

    /// Directions or a loop. Two words, because the difference is whether the
    /// user has a destination in mind.
    private var modePicker: some View {
        Picker("What to plan", selection: $model.mode) {
            ForEach(PlanningMode.allCases) { mode in
                Text(mode.rawValue).tag(mode)
            }
        }
        .pickerStyle(.segmented)
    }

    /// The original planning content: two addresses, the preference slider and
    /// the route comparison.
    @ViewBuilder private var directionsContent: some View {
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

    /// Title + a short hint, with the Tune button and (once something is set)
    /// swap/clear buttons.
    private var header: some View {
        HStack(spacing: 14) {
            VStack(alignment: .leading, spacing: 1) {
                Text("Scenic").font(.title2.bold())
                Text(hint).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            // Tune is always available — the accent tint signals an active
            // preference so the user knows their routes are being shaped.
            Button { showingTune = true } label: {
                Label("Tune", systemImage: "slider.horizontal.3").font(.subheadline)
            }
            .tint(model.isTuned ? .scenic : .secondary)
            // Swap and clear belong to a trip with two ends. A loop has one,
            // and its own clear button lives in its start field.
            if model.mode == .directions, model.start != nil || model.end != nil {
                Button { model.swapEnds() } label: { Image(systemName: "arrow.up.arrow.down") }
                    .disabled(model.start == nil || model.end == nil)
                Button {
                    model.clear()
                    detent = .planningCompact
                } label: { Image(systemName: "xmark.circle") }
            }
        }
    }

    private var hint: String {
        switch model.mode {
        case .directions:
            return model.response == nil
                ? "Search for a start and destination"
                : "Drag the slider to trade time for scenery"
        case .loops:
            return model.loops.response == nil
                ? "A scenic loop back to where you started"
                : "Shuffle for a different direction"
        }
    }

    /// One address row: a colored dot, a field that drives live autocomplete as
    /// the user types, and (when focused) suggestions beneath it. The start row
    /// also carries the "use where I am" button.
    private func searchField(
        _ prompt: String, _ text: Binding<String>, dot: Color, role: Endpoint
    ) -> some View {
        VStack(spacing: 6) {
            HStack(spacing: 8) {
                Circle().fill(dot).frame(width: 9, height: 9)
                TextField(prompt, text: text)
                    .focused($focused, equals: role)
                    .submitLabel(.search)
                    .autocorrectionDisabled()
                    // Feed each keystroke to the completer (only for the field
                    // actually being typed in, not programmatic label updates),
                    // ranked around whatever the map is showing.
                    .onChange(of: text.wrappedValue) { _, newValue in
                        if focused == role {
                            completer.update(for: newValue, near: model.searchRegion)
                        }
                    }
                    // Return key still works as a fallback for a raw query.
                    .onSubmit {
                        focused = nil
                        completer.clear()
                        Task { await model.search(text.wrappedValue, into: role) }
                    }
                if role == .start { myLocationButton }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 11)
            .background(.quaternary, in: RoundedRectangle(cornerRadius: 10))

            if focused == role {
                suggestionList(for: role)
            }
        }
    }

    /// Fill the start with wherever the driver is standing. It sits in the field
    /// itself, not just in the dropdown, because "route me from here" is the
    /// common case and shouldn't need a tap to discover.
    private var myLocationButton: some View {
        Button {
            focused = nil
            completer.clear()
            Task { await model.useMyLocation() }
        } label: {
            Group {
                if model.isLocatingUser {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: "location.fill")
                }
            }
            .frame(width: 30, height: 30)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.scenic)
        .disabled(model.isLocatingUser)
        .accessibilityLabel("Start from my current location")
    }

    /// The autocomplete dropdown. Tapping a row resolves it to a place, sets the
    /// endpoint, and routes immediately once both ends are filled. The start
    /// field gets a "My Location" row on top, the way a maps app should.
    @ViewBuilder private func suggestionList(for role: Endpoint) -> some View {
        let rows = Array(completer.suggestions.prefix(5).enumerated())
        if role == .start || !rows.isEmpty {
            VStack(spacing: 0) {
                if role == .start {
                    Button {
                        focused = nil
                        completer.clear()
                        Task { await model.useMyLocation() }
                    } label: {
                        Label("My Location", systemImage: "location.fill")
                            .foregroundStyle(Color.scenic)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.vertical, 8)
                            .padding(.horizontal, 12)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)

                    if !rows.isEmpty { Divider() }
                }

                ForEach(rows, id: \.offset) { index, suggestion in
                    Button {
                        focused = nil
                        completer.clear()
                        Task { await model.choose(suggestion, into: role) }
                    } label: {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(suggestion.title)
                            if !suggestion.subtitle.isEmpty {
                                Text(suggestion.subtitle)
                                    .font(.caption2).foregroundStyle(.secondary)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 8)
                        .padding(.horizontal, 12)
                        // Without an explicit shape, only the *text* is
                        // tappable — the empty space to the right of a short
                        // name like "Rockport, MA" isn't part of the button, so
                        // half the row silently ignores taps.
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)

                    if index < rows.count - 1 { Divider() }
                }
            }
            .background(.quaternary, in: RoundedRectangle(cornerRadius: 10))
        }
    }

    /// Where the slider's handle sits, which is deliberately *not* `model.pref`.
    ///
    /// See `PrefSlider` for why. `model.pref` is the API's number and stays it;
    /// this binding is the only place the handle's own coordinate exists.
    private var prefPosition: Binding<Double> {
        Binding(get: { PrefSlider.position(forPref: model.pref) },
                set: { model.pref = PrefSlider.pref(atPosition: $0) })
    }

    /// Fastest-to-scenic slider. Re-routes only when the user lets go, so we
    /// don't hammer the backend mid-drag.
    private var prefSlider: some View {
        VStack(spacing: 2) {
            HStack {
                Text("Fastest").font(.caption2)
                Slider(value: prefPosition, in: 0...1) { editing in
                    if !editing { Task { await model.computeRoute() } }
                }
                Text("Scenic").font(.caption2)
            }
            // The position, not `model.pref` — after the mapping those are two
            // different numbers and printing the one the handle is not at is
            // how the caption would start lying. "Strength" because under the
            // mapping the position *is* the router's `strength`.
            Text("scenery strength \(prefPosition.wrappedValue, format: .number.precision(.fractionLength(2)))")
                .font(.caption2).foregroundStyle(.secondary)
        }
    }

    /// Begins live navigation along the scenic route.
    private func startButton(for response: RouteResponse) -> some View {
        Button {
            model.startNavigation(response.scenic)
        } label: {
            Label("Start scenic drive", systemImage: "location.north.line.fill")
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(.borderedProminent)
        .tint(.scenic)
    }
}
