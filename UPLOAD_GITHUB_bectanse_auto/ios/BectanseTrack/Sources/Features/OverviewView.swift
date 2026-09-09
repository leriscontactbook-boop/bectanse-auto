import Charts
import SwiftUI

struct OverviewView: View {
    @EnvironmentObject private var store: AppStore
    let connect: () -> Void

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 18) {
                greeting
                if store.accounts.isEmpty {
                    emptyAccount
                } else {
                    accountStrip
                    performance
                    equityChart
                    monthSnapshot
                    coachPreview
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 20)
            .padding(.bottom, 18)
        }
        .scrollIndicators(.hidden)
        .task {
            if store.overview == nil { await store.loadOverview() }
            if store.coach == nil { await store.loadCoach("daily") }
        }
    }

    private var greeting: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text("ESPACE DE PERFORMANCE PRIVÉ")
                .font(.trackLabel(9)).tracking(2).foregroundStyle(Brand.secondaryText)
            Text("Bonjour \(store.member?.firstName.uppercased() ?? "TRADER")")
                .font(.trackDisplay(36))
            Text(store.entitlements.source == "ACADEMY_INCLUDED" ? "BECTANSE MEMBER · JOURNAL INCLUS" : "BECTANSE TRACK · ACCÈS ACTIF")
                .font(.trackLabel(8)).tracking(1.5).foregroundStyle(Brand.orange)
        }
    }

    private var emptyAccount: some View {
        VStack(alignment: .leading, spacing: 18) {
            Image(systemName: "link.badge.plus")
                .font(.system(size: 28, weight: .semibold))
                .foregroundStyle(Brand.orange)
            Text("Connectez votre premier compte MT5")
                .font(.trackDisplay(30))
            Text("Votre historique, votre calendrier et vos analyses se construisent automatiquement à partir des données vérifiées du broker.")
                .foregroundStyle(Brand.secondaryText)
                .lineSpacing(4)
            Button("Connecter un compte", action: connect)
                .buttonStyle(PrimaryButtonStyle())
        }
        .trackCard()
    }

    private var accountStrip: some View {
        VStack(alignment: .leading, spacing: 13) {
            HStack(spacing: 10) {
                Circle()
                    .fill(store.activeAccount?.isSynced == true ? Brand.positive : Brand.orange)
                    .frame(width: 8, height: 8)
                    .shadow(color: store.activeAccount?.isSynced == true ? Brand.positive : Brand.orange, radius: 7)
                VStack(alignment: .leading, spacing: 3) {
                    Text("\(store.activeAccount?.broker ?? "MetaTrader 5") · \(store.activeAccount?.loginMasked ?? "")")
                        .font(.subheadline.weight(.bold))
                    Text(accountStatus)
                        .font(.caption)
                        .foregroundStyle(Brand.secondaryText)
                        .lineLimit(1)
                }
                Spacer()
                Button {
                    Task { await store.syncNow() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 16, weight: .semibold))
                        .frame(width: 44, height: 44)
                        .background(Brand.raised)
                        .clipShape(RoundedRectangle(cornerRadius: 13))
                }
                .foregroundStyle(Brand.primaryText)
                .accessibilityLabel("Synchroniser")
            }
            if store.accounts.count > 1 {
                Menu {
                    ForEach(store.accounts) { account in
                        Button(account.title + " · " + account.loginMasked) {
                            Task { await store.selectAccount(account.id) }
                        }
                    }
                } label: {
                    HStack {
                        Text(store.activeAccount?.title ?? "Compte")
                        Spacer()
                        Image(systemName: "chevron.up.chevron.down")
                    }
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Brand.primaryText)
                    .trackField()
                }
            }
        }
        .trackCard(padding: 15)
    }

    private var accountStatus: String {
        guard let account = store.activeAccount else { return "Compte indisponible" }
        if let error = account.lastErrorMessage, !error.isEmpty { return error }
        if account.isSynced, let raw = account.lastSuccessfulSyncAt {
            return "Synchronisé · \(TrackFormat.dateTime(raw))"
        }
        return account.syncStatus.replacingOccurrences(of: "_", with: " ").localizedCapitalized
    }

    @ViewBuilder private var performance: some View {
        if let stats = store.overview?.stats {
            SectionHeading(eyebrow: "Performance", title: "Vue d’ensemble", trailing: "Données vérifiées")
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                MetricTile(label: "P&L TOTAL", value: TrackFormat.money(stats.netPnl, currency: stats.currency, signed: true), tone: stats.netPnl >= 0 ? Brand.positive : Brand.negative)
                MetricTile(label: "BALANCE", value: TrackFormat.money(stats.balance, currency: stats.currency))
                MetricTile(label: "EQUITY", value: TrackFormat.money(stats.equity, currency: stats.currency))
                MetricTile(label: "WIN RATE", value: TrackFormat.percent(stats.winRate), detail: "\(stats.wins) positions gagnantes")
            }
        } else if store.isRefreshing {
            ProgressView().tint(Brand.orange).frame(maxWidth: .infinity).padding(40)
        }
    }

    @ViewBuilder private var equityChart: some View {
        if let equity = store.overview?.equity {
            VStack(alignment: .leading, spacing: 15) {
                HStack {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("COURBE DE PERFORMANCE").font(.trackLabel(9)).tracking(1.6).foregroundStyle(Brand.secondaryText)
                        Text("P&L cumulé").font(.title3.bold())
                    }
                    Spacer()
                    Text("\(equity.tradeCount) positions").font(.caption).foregroundStyle(Brand.secondaryText)
                }
                if equity.points.count > 1 {
                    Chart(equity.points) { point in
                        AreaMark(
                            x: .value("Position", point.tradeIndex ?? 0),
                            y: .value("P&L", point.cumulativePnl)
                        )
                        .foregroundStyle(LinearGradient(colors: [Brand.orange.opacity(0.28), .clear], startPoint: .top, endPoint: .bottom))
                        LineMark(
                            x: .value("Position", point.tradeIndex ?? 0),
                            y: .value("P&L", point.cumulativePnl)
                        )
                        .foregroundStyle(Brand.orange)
                        .lineStyle(StrokeStyle(lineWidth: 2.2, lineCap: .round, lineJoin: .round))
                    }
                    .chartXAxis(.hidden)
                    .chartYAxis(.hidden)
                    .frame(height: 210)
                } else {
                    EmptyPanel(title: "Courbe en préparation", message: "Elle apparaîtra dès la première position clôturée synchronisée.")
                }
            }
            .trackCard()
        }
    }

    @ViewBuilder private var monthSnapshot: some View {
        if let calendar = store.overview?.calendar {
            VStack(alignment: .leading, spacing: 15) {
                SectionHeading(eyebrow: "Régularité", title: "Ce mois-ci")
                HStack(spacing: 7) {
                    ForEach(recentSevenDays(calendar.days)) { day in
                        VStack(spacing: 8) {
                            Text(shortDay(day.date)).font(.trackLabel(8)).foregroundStyle(Brand.secondaryText)
                            Text(day.netPnl == 0 ? "—" : compactMoney(day.netPnl))
                                .font(.system(size: 10, weight: .bold, design: .rounded))
                                .foregroundStyle(day.netPnl >= 0 ? Brand.positive : Brand.negative)
                                .minimumScaleFactor(0.6)
                                .lineLimit(1)
                        }
                        .frame(maxWidth: .infinity, minHeight: 66)
                        .background(day.netPnl == 0 ? Brand.raised.opacity(0.35) : (day.netPnl > 0 ? Brand.positive.opacity(0.07) : Brand.negative.opacity(0.07)))
                        .clipShape(RoundedRectangle(cornerRadius: 11))
                    }
                }
            }
            .trackCard()
        }
    }

    private var coachPreview: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("BECTANSE COACH").font(.trackLabel(9)).tracking(1.8).foregroundStyle(Brand.orange)
                Spacer()
                Image(systemName: "sparkles").foregroundStyle(Brand.orange)
            }
            Text(store.coach?.insights.first?.title ?? "Votre analyste de performance privé")
                .font(.trackDisplay(27))
            Text(store.coach?.insights.first?.recommendation ?? store.coach?.summary.mainImprovement ?? "Le Coach attend suffisamment de données avant d’afficher une conclusion.")
                .font(.subheadline)
                .foregroundStyle(Brand.secondaryText)
                .lineSpacing(3)
            Button("Ouvrir le Coach") { store.selectedTab = .coach }
                .font(.subheadline.bold())
                .foregroundStyle(Brand.orange)
        }
        .trackCard()
    }

    private func recentSevenDays(_ days: [TradingDay]) -> [TradingDay] {
        let byDate = Dictionary(uniqueKeysWithValues: days.map { ($0.date, $0) })
        let formatter = DateFormatter(); formatter.dateFormat = "yyyy-MM-dd"; formatter.locale = Locale(identifier: "en_US_POSIX")
        return (0..<7).reversed().compactMap { offset in
            guard let date = Calendar.current.date(byAdding: .day, value: -offset, to: Date()) else { return nil }
            let key = formatter.string(from: date)
            return byDate[key] ?? TradingDay(date: key, netPnl: 0, trades: 0, wins: 0, losses: 0, unmatched: 0)
        }
    }

    private func shortDay(_ raw: String) -> String {
        let parser = DateFormatter(); parser.dateFormat = "yyyy-MM-dd"; parser.locale = Locale(identifier: "en_US_POSIX")
        guard let date = parser.date(from: raw) else { return "—" }
        let formatter = DateFormatter(); formatter.locale = Locale(identifier: "fr_FR"); formatter.dateFormat = "EE dd"
        return formatter.string(from: date).uppercased()
    }

    private func compactMoney(_ value: Double) -> String {
        let sign = value > 0 ? "+" : ""
        if abs(value) >= 1000 { return sign + String(format: "%.1fk", value / 1000) }
        return sign + String(format: "%.0f", value)
    }
}
