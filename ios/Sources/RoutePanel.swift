import MapKit
import SwiftUI

/// The bottom-sheet panel: two address searches, the preference slider, the
/// route comparison once both ends are set, and a button to start driving. It's
/// a plain scrolling stack — the sheet itself decides how much is visible.
struct RoutePanel: View {
    @Bindable var model: RouteModel

    /// Drives the live autocomplete dropdown.
    @State private var completer = SearchCompleter()
    /// Which field (if any) the user is typing in — so suggestions show under
    /// the right one.
    @FocusState private var focused: Endpoint?
    /// Whether the "Tune scenery" sheet is showing.
    @State private var showingTune = false

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
                    startButton(for: response)
                }
            }
            .padding(20)
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
                Button { model.clear() } label: { Image(systemName: "xmark.circle") }
            }
        }
    }

    /// One address row: a colored dot, a field that drives live autocomplete as
    /// the user types, and (when focused) a dropdown of suggestions beneath it.
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
                    // actually being typed in, not programmatic label updates).
                    .onChange(of: text.wrappedValue) { _, newValue in
                        if focused == role { completer.update(for: newValue) }
                    }
                    // Return key still works as a fallback for a raw query.
                    .onSubmit {
                        focused = nil
                        completer.clear()
                        Task { await model.search(text.wrappedValue, into: role) }
                    }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 11)
            .background(.quaternary, in: RoundedRectangle(cornerRadius: 10))

            if focused == role && !completer.suggestions.isEmpty {
                suggestionList(for: role)
            }
        }
    }

    /// The autocomplete dropdown. Tapping a row resolves it to a place, sets the
    /// endpoint, and routes immediately once both ends are filled.
    private func suggestionList(for role: Endpoint) -> some View {
        let rows = Array(completer.suggestions.prefix(5).enumerated())
        return VStack(spacing: 0) {
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
                }
                .buttonStyle(.plain)

                if index < rows.count - 1 { Divider() }
            }
        }
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
        .padding(.top, 4)
    }
}
