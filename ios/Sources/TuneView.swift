import SwiftUI

/// The "Tune scenery" sheet: one slider per beauty type, letting the user say
/// what kind of scenery they want more — or less — of.
///
/// Editing a slider re-routes in the background, so the updated route is already
/// waiting when the sheet is closed. The main Fastest↔Scenic slider still sets
/// the overall strength; these only shape *what kind* of scenery it chases.
struct TuneView: View {
    @Bindable var model: RouteModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    Text("Drag toward what you'd rather drive past. Centered means no preference.")
                        .font(.caption)
                        .foregroundStyle(.secondary)

                    ForEach(BeautyType.all) { type in
                        slider(for: type)
                    }
                }
                .padding(20)
            }
            .navigationTitle("Tune scenery")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Reset") { model.resetWeights() }
                        .disabled(!model.isTuned)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }

    /// One labeled slider bound to a beauty type's weight. Re-routes when the
    /// user lets go (not on every pixel of the drag), so we don't spam the
    /// backend mid-gesture.
    private func slider(for type: BeautyType) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(type.label).font(.subheadline.weight(.medium))
                Spacer()
                Text(emphasis(for: type.apiName))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            HStack(spacing: 8) {
                Image(systemName: "minus").font(.caption2).foregroundStyle(.tertiary)
                Slider(value: binding(for: type), in: BeautyType.weightRange) { editing in
                    if !editing {
                        Task { await model.computeRoute() }
                    }
                }
                .tint(.scenic)
                Image(systemName: "plus").font(.caption2).foregroundStyle(.tertiary)
            }
        }
    }

    /// A binding into the model's weight dictionary for one type.
    private func binding(for type: BeautyType) -> Binding<Double> {
        Binding(
            get: { model.weights[type.apiName] ?? BeautyType.neutralWeight },
            set: { model.weights[type.apiName] = $0 }
        )
    }

    /// A one-word hint of where a slider sits relative to neutral.
    private func emphasis(for apiName: String) -> String {
        let weight = model.weights[apiName] ?? BeautyType.neutralWeight
        if weight > BeautyType.neutralWeight + 0.05 { return "more" }
        if weight < BeautyType.neutralWeight - 0.05 { return "less" }
        return ""
    }
}
