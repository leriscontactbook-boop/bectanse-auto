import SwiftUI

struct CoachView: View {
    @EnvironmentObject private var store: AppStore
    @State private var review = "daily"

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 18) {
                SectionHeading(eyebrow: "Analyste privé", title: "Bectanse Coach", trailing: "Moteur local")
                reviewSelector
                if let coach = store.coach {
                    scorePanel(coach)
                    improvement(coach)
                    if coach.insights.isEmpty {
                        EmptyPanel(
                            title: "Aucune conclusion prématurée",
                            message: "Le Coach attend un échantillon suffisant. Il ne crée jamais de faits financiers absents de vos données."
                        )
                    } else {
                        ForEach(coach.insights.prefix(8)) { CoachInsightCard(insight: $0, currency: store.overview?.stats.currency ?? "EUR") }
                    }
                } else {
                    ProgressView().tint(Brand.orange).frame(maxWidth: .infinity).padding(60)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 20)
            .padding(.bottom, 18)
        }
        .scrollIndicators(.hidden)
        .task { if store.coach == nil { await store.loadCoach(review) } }
    }

    private var reviewSelector: some View {
        HStack(spacing: 8) {
            reviewButton("Aujourd’hui", value: "daily", feature: "coach.daily")
            reviewButton("Semaine", value: "weekly", feature: "coach.weekly")
            reviewButton("Mois", value: "monthly", feature: "coach.monthly")
        }
    }

    private func reviewButton(_ label: String, value: String, feature: String) -> some View {
        let enabled = store.entitlements.features[feature] == true
        return Button {
            guard enabled else { return }
            review = value
            Task { await store.loadCoach(value) }
        } label: {
            Text(label)
                .font(.system(size: 12, weight: .bold, design: .rounded))
                .frame(maxWidth: .infinity, minHeight: 44)
                .foregroundStyle(review == value ? .black : enabled ? Brand.primaryText : Brand.secondaryText)
                .background(review == value ? Brand.orange : Brand.surface)
                .clipShape(RoundedRectangle(cornerRadius: 13))
                .overlay(RoundedRectangle(cornerRadius: 13).stroke(Brand.line))
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
    }

    private func scorePanel(_ coach: CoachResponse) -> some View {
        VStack(spacing: 20) {
            HStack(spacing: 22) {
                ZStack {
                    Circle().stroke(Brand.line, lineWidth: 9)
                    Circle()
                        .trim(from: 0, to: (coach.score.score ?? 0) / 100)
                        .stroke(Brand.orange, style: StrokeStyle(lineWidth: 9, lineCap: .round))
                        .rotationEffect(.degrees(-90))
                    VStack(spacing: 1) {
                        Text(coach.score.available ? "\(Int((coach.score.score ?? 0).rounded()))" : "—")
                            .font(.trackDisplay(38))
                        Text("SUR 100").font(.trackLabel(7)).tracking(1).foregroundStyle(Brand.secondaryText)
                    }
                }
                .frame(width: 122, height: 122)
                VStack(alignment: .leading, spacing: 7) {
                    Text("TRADING SCORE").font(.trackLabel(9)).tracking(1.6).foregroundStyle(Brand.orange)
                    Text(coach.score.available ? "Score comportemental" : "Analyse en construction")
                        .font(.title3.bold())
                    Text("\(coach.score.sampleSize) trades · \(coach.telemetry.eventsAnalyzed) événements MT5")
                        .font(.caption).foregroundStyle(Brand.secondaryText)
                }
            }
            if coach.score.available {
                VStack(spacing: 11) {
                    ForEach(componentOrder, id: \.key) { item in
                        ScoreBar(label: item.label, value: coach.score.components[item.key] ?? 0)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity)
        .trackCard()
    }

    private func improvement(_ coach: CoachResponse) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("PRIORITÉ").font(.trackLabel(9)).tracking(1.5).foregroundStyle(Brand.orange)
            Text("Votre prochain levier").font(.trackDisplay(28))
            Text(coach.summary.mainImprovement).font(.subheadline).foregroundStyle(Brand.secondaryText).lineSpacing(3)
        }
        .trackCard()
    }

    private var componentOrder: [(key: String, label: String)] {
        [("discipline", "Discipline"), ("risk", "Risque"), ("consistency", "Régularité"),
         ("execution", "Exécution"), ("timing", "Timing")]
    }
}
private struct ScoreBar: View {
    let label: String
    let value: Double
    var body: some View {
        HStack(spacing: 10) {
            Text(label).font(.caption.weight(.semibold)).frame(width: 72, alignment: .leading)
            GeometryReader { proxy in
                ZStack(alignment: .leading) {
                    Capsule().fill(Brand.line)
                    Capsule().fill(Brand.orange).frame(width: proxy.size.width * value / 100)
                }
            }
            .frame(height: 5)
            Text("\(Int(value.rounded()))").font(.caption.monospacedDigit()).frame(width: 24, alignment: .trailing)
        }
    }
}

private struct CoachInsightCard: View {
    let insight: CoachInsight
    let currency: String
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(insight.severity)
                    .font(.trackLabel(7)).tracking(1)
                    .foregroundStyle(severityColor)
                    .padding(.horizontal, 8).padding(.vertical, 5)
                    .background(severityColor.opacity(0.10)).clipShape(Capsule())
                Spacer()
                Text("\(Int((insight.confidence * 100).rounded())) % · \(insight.sampleSize) observations")
                    .font(.caption2).foregroundStyle(Brand.secondaryText)
            }
            Text(insight.title).font(.title3.bold())
            Text(insight.recommendation).font(.subheadline).foregroundStyle(Brand.secondaryText).lineSpacing(3)
            if let impact = insight.financialImpactIfMeasurable {
                Text("Impact mesuré " + TrackFormat.money(impact, currency: currency, signed: true))
                    .font(.caption.bold())
                    .foregroundStyle(impact >= 0 ? Brand.positive : Brand.negative)
            }
        }
        .trackCard()
    }

    private var severityColor: Color {
        insight.severity == "HIGH" ? Brand.negative : insight.severity == "MEDIUM" ? Brand.orange : Brand.positive
    }
}
