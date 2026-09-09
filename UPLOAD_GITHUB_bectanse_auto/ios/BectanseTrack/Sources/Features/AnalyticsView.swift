import SwiftUI

struct AnalyticsView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 17) {
                SectionHeading(eyebrow: "Comprendre", title: "Analytics", trailing: "ELITE")
                if !store.entitlements.advancedAnalytics {
                    EmptyPanel(title: "Analytics ELITE", message: "Cette analyse détaillée n’est pas incluse dans votre formule actuelle.")
                } else if let analytics = store.analytics {
                    RankingPanel(title: "Actifs", subtitle: "Résultat par symbole", rows: analytics.symbol, currency: analytics.currency)
                    RankingPanel(title: "Sessions", subtitle: "Performance par session", rows: analytics.session, currency: analytics.currency)
                    RankingPanel(title: "Jours", subtitle: "Performance par jour", rows: analytics.weekday, currency: analytics.currency)
                    RankingPanel(title: "Durées", subtitle: "Temps en position", rows: analytics.holdingTime, currency: analytics.currency)
                    RankingPanel(title: "Direction", subtitle: "Buy et Sell", rows: analytics.direction, currency: analytics.currency)
                    RankingPanel(title: "Évolution", subtitle: "Résultat mensuel", rows: analytics.month, currency: analytics.currency)
                } else {
                    ProgressView().tint(Brand.orange).frame(maxWidth: .infinity).padding(60)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 20)
            .padding(.bottom, 18)
        }
        .scrollIndicators(.hidden)
        .task { if store.analytics == nil { await store.loadAnalytics() } }
    }
}
private struct RankingPanel: View {
    let title: String
    let subtitle: String
    let rows: [AnalyticsRow]
    let currency: String

    var body: some View {
        VStack(alignment: .leading, spacing: 15) {
            VStack(alignment: .leading, spacing: 3) {
                Text(subtitle.uppercased()).font(.trackLabel(8)).tracking(1.4).foregroundStyle(Brand.secondaryText)
                Text(title).font(.trackDisplay(27))
            }
            if rows.isEmpty {
                Text("Pas encore assez de données.").font(.subheadline).foregroundStyle(Brand.secondaryText)
            } else {
                ForEach(Array(rows.prefix(6).enumerated()), id: \.element.id) { index, row in
                    HStack(spacing: 12) {
                        Text(String(format: "%02d", index + 1))
                            .font(.trackLabel(8)).foregroundStyle(Brand.secondaryText)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(display(row.label)).font(.subheadline.weight(.bold))
                            Text("\(row.trades) positions · \(TrackFormat.percent(row.winRate))")
                                .font(.caption).foregroundStyle(Brand.secondaryText)
                        }
                        Spacer()
                        Text(TrackFormat.money(row.netPnl, currency: currency, signed: true))
                            .font(.subheadline.weight(.bold))
                            .foregroundStyle(row.netPnl >= 0 ? Brand.positive : Brand.negative)
                    }
                    if index < min(rows.count, 6) - 1 { Rectangle().fill(Brand.line).frame(height: 1) }
                }
            }
        }
        .trackCard()
    }

    private func display(_ raw: String) -> String {
        raw.replacingOccurrences(of: "_", with: " ").localizedCapitalized
    }
}
