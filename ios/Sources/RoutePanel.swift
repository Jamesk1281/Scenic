import MapKit
import SwiftUI

extension PresentationDetent {
    /// The planning sheet's resting height before the user engages with it:
    /// the two fields and the slider, and not much more.
    static let planningCompact = PresentationDetent.height(260)
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

            if let response = model.response {
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
    }

    /// Title + a short hint, with the Tune button and (once something is set)
    /// swap/clear buttons.
    private var header: some View {
        HStack(spacing: 14) {
            VStack(alignment: .leading, spacing: 1) {
                Text("Scenic").font(.title2.bold())
                Text(model.response == nil
                     ? "Search for a start and destination"
                     : "Drag the slider to trade time for scenery")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            // Tune is always available — the accent tint signals an active
            // preference so the user knows their routes are being shaped.
            Button { showingTune = true } label: {
                Label("Tune", systemImage: "slider.horizontal.3").font(.subheadline)
            }
            .tint(model.isTuned ? .scenic : .secondary)
            if model.start != nil || model.end != nil {
                Button { model.swapEnds() } label: { Image(systemName: "arrow.up.arrow.down") }
                    .disabled(model.start == nil || model.end == nil)
                Button {
                    model.clear()
                    detent = .planningCompact
                } label: { Image(systemName: "xmark.circle") }
            }
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
