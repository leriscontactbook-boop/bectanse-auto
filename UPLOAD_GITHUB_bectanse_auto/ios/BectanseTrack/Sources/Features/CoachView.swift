import SwiftUI

struct CoachView: View {
    @EnvironmentObject private var store: AppStore
    @State private var review = "daily"

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                SectionHeading(eyebrow: "Analyste privé", title: "Bectanse Coach", trailing: "Analyse déterministe")
                reviewSelector
                if let error = store.coachLoadError {
                    EmptyPanel(title: "Analyse indisponible", message: error)
                    Button("Réessayer") { Task { await store.loadCoach(review) } }
                        .buttonStyle(PrimaryButtonStyle())
                } else if store.isLoadingCoach || store.coach?.reviewType != review {
                    LoadingPanel()
                } else if let coach = store.coach {
                    periodPanel(coach)
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
                    LoadingPanel()
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 20)
            .padding(.bottom, 18)
        }
        .scrollIndicators(.hidden)
        .task(id: review) { await store.loadCoach(review) }
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
            Tactile.selection()
            review = value
        } label: {
            VStack(spacing: 6) {
                Text(label)
                    .font(TrackType.body(12, weight: .semibold))
                Capsule().fill(review == value ? Brand.orange : .clear).frame(width: 18, height: 2)
            }
            .frame(maxWidth: .infinity, minHeight: 44)
            .foregroundStyle(review == value ? Brand.orange : enabled ? Brand.primaryText : Brand.secondaryText)
            .background(review == value ? Brand.orange.opacity(0.065) : Brand.surface)
            .clipShape(RoundedRectangle(cornerRadius: Brand.Radius.control, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Brand.Radius.control, style: .continuous).stroke(review == value ? Brand.orange.opacity(0.30) : Brand.line, lineWidth: 0.75))
        }
        .buttonStyle(TactileCardButtonStyle())
        .disabled(!enabled)
        .accessibilityAddTraits(review == value ? .isSelected : [])
        .accessibilityValue(enabled ? "Disponible" : "Non inclus dans cette formule")
    }

    private func periodPanel(_ coach: CoachResponse) -> some View {
        let performance = coach.periodPerformance
        let currency = store.overview?.stats.currency ?? "EUR"
        return VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 5) {
                    Text("BILAN \(reviewLabel.uppercased())")
                        .font(.trackLabel(8.5))
                        .tracking(1.35)
                        .foregroundStyle(Brand.orange)
                    Text(periodRange(coach.period))
                        .font(TrackType.body(12))
                        .foregroundStyle(Brand.secondaryText)
                }
                Spacer()
                if store.isLoadingCoach {
                    ProgressView().tint(Brand.orange)
                }
            }
            HStack(spacing: 0) {
                CoachPeriodMetric(
                    label: "P&L NET",
                    value: TrackFormat.money(performance?.netPnl ?? 0, currency: currency, signed: true),
                    color: (performance?.netPnl ?? 0) >= 0 ? Brand.positive : Brand.negative
                )
                Rectangle().fill(Brand.line).frame(width: 1, height: 46)
                CoachPeriodMetric(label: "POSITIONS", value: "\(performance?.trades ?? 0)")
                Rectangle().fill(Brand.line).frame(width: 1, height: 46)
                CoachPeriodMetric(
                    label: "ÉVOLUTION",
                    value: TrackFormat.money(performance?.pnlChange ?? 0, currency: currency, signed: true),
                    color: (performance?.pnlChange ?? 0) >= 0 ? Brand.positive : Brand.negative
                )
            }
            Text("Comparaison avec \(previousPeriodLabel) · \(performance?.previousTrades ?? 0) positions")
                .font(TrackType.body(11))
                .foregroundStyle(Brand.mutedText)
        }
        .trackCard(padding: 16, highlighted: true)
    }

    private var reviewLabel: String {
        switch review {
        case "weekly": "de la semaine"
        case "monthly": "du mois"
        default: "d’aujourd’hui"
        }
    }

    private var previousPeriodLabel: String {
        switch review {
        case "weekly": "la semaine précédente"
        case "monthly": "le mois précédent"
        default: "la journée précédente"
        }
    }

    private func periodRange(_ period: CoachPeriod?) -> String {
        guard let period,
              let start = ISO8601DateFormatter().date(from: period.from),
              let end = ISO8601DateFormatter().date(from: period.to)
        else { return "Données MT5 vérifiées" }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "fr_FR")
        formatter.dateFormat = review == "daily" ? "d MMMM yyyy" : "d MMM"
        if review == "daily" { return formatter.string(from: end).uppercased() }
        return "\(formatter.string(from: start)) — \(formatter.string(from: end))".uppercased()
    }

    private func scorePanel(_ coach: CoachResponse) -> some View {
        VStack(spacing: 20) {
            HStack(spacing: 22) {
                ZStack {
                    Circle().stroke(Brand.line, lineWidth: 7)
                    Circle()
                        .trim(from: 0, to: (coach.score.score ?? 0) / 100)
                        .stroke(
                            AngularGradient(colors: [Brand.orangeSoft, Brand.orange, Color(red: 1, green: 0.62, blue: 0.24)], center: .center),
                            style: StrokeStyle(lineWidth: 7, lineCap: .round)
                        )
                        .rotationEffect(.degrees(-90))
                        .shadow(color: Brand.orange.opacity(0.18), radius: 8)
                    VStack(spacing: 1) {
                        Text(coach.score.available ? "\(Int((coach.score.score ?? 0).rounded()))" : "—")
                            .font(.trackMetric(36))
                            .tracking(-1)
                        Text("SUR 100").font(.trackLabel(7)).tracking(1).foregroundStyle(Brand.secondaryText)
                    }
                }
                .frame(width: 122, height: 122)
                VStack(alignment: .leading, spacing: 7) {
                    HStack(spacing: 7) {
                        ZStack {
                            Circle().fill(Brand.orange.opacity(0.14)).frame(width: 15, height: 15)
                            Circle().fill(Brand.orange).frame(width: 5, height: 5)
                        }
                        .frame(width: 15, height: 15)
                        Text("TRADING SCORE").font(.trackLabel(9)).tracking(1.35).foregroundStyle(Brand.orange)
                    }
                    Text(coach.score.available ? "Score comportemental" : "Analyse en construction")
                        .font(TrackType.heading(18))
                        .tracking(-0.3)
                    Text("\(coach.score.sampleSize) trades · \(coach.telemetry.eventsAnalyzed) événements MT5")
                        .font(TrackType.body(12)).foregroundStyle(Brand.secondaryText)
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
            Text("Votre prochain levier").font(.trackDisplay(24)).tracking(-0.5)
            Text(coach.summary.mainImprovement).font(TrackType.body(14)).foregroundStyle(Brand.secondaryText).lineSpacing(3)
        }
        .trackCard()
    }

    private var componentOrder: [(key: String, label: String)] {
        [("discipline", "Discipline"), ("risk", "Risque"), ("consistency", "Régularité"),
         ("execution", "Exécution"), ("timing", "Timing")]
    }
}

private struct CoachPeriodMetric: View {
    let label: String
    let value: String
    var color: Color = Brand.primaryText

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(.trackLabel(7))
                .tracking(0.8)
                .foregroundStyle(Brand.mutedText)
                .lineLimit(1)
            Text(value)
                .font(.trackMetric(15))
                .foregroundStyle(color)
                .lineLimit(1)
                .minimumScaleFactor(0.55)
                .contentTransition(.numericText())
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 10)
    }
}

private struct ScoreBar: View {
    let label: String
    let value: Double
    var body: some View {
        HStack(spacing: 10) {
            Text(label).font(.trackLabel(10)).frame(width: 72, alignment: .leading)
            GeometryReader { proxy in
                ZStack(alignment: .leading) {
                    Capsule().fill(Brand.line)
                    Capsule().fill(Brand.orange).frame(width: proxy.size.width * value / 100)
                }
            }
            .frame(height: 5)
            Text("\(Int(value.rounded()))").font(.trackMetric(10)).frame(width: 24, alignment: .trailing)
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
                    .font(.trackLabel(9)).monospacedDigit().foregroundStyle(Brand.secondaryText)
            }
            Text(insight.title).font(TrackType.heading(18)).tracking(-0.3)
            Text(insight.observation)
                .font(TrackType.body(14))
                .foregroundStyle(Brand.primaryText.opacity(0.82))
                .lineSpacing(3)
            Rectangle().fill(Brand.line).frame(height: 1)
            VStack(alignment: .leading, spacing: 5) {
                Text("RECOMMANDATION").font(.trackLabel(7.5)).tracking(1).foregroundStyle(Brand.orange)
                Text(insight.recommendation).font(TrackType.body(14)).foregroundStyle(Brand.secondaryText).lineSpacing(3)
            }
            if let impact = insight.financialImpactIfMeasurable {
                Text("Impact mesuré " + TrackFormat.money(impact, currency: currency, signed: true))
                    .font(.trackMetric(12))
                    .foregroundStyle(impact >= 0 ? Brand.positive : Brand.negative)
            }
        }
        .trackCard()
    }

    private var severityColor: Color {
        insight.severity == "HIGH" ? Brand.negative : insight.severity == "MEDIUM" ? Brand.orange : Brand.positive
    }
}
