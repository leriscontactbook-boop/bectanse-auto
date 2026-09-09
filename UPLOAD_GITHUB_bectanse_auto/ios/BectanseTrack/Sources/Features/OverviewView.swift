import Charts
import SwiftUI

struct OverviewView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var appeared = false
    @State private var selectedTradeIndex: Int?
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
            .padding(.top, 18)
            .padding(.bottom, 18)
            .opacity(appeared ? 1 : 0.01)
            .offset(y: appeared ? 0 : (reduceMotion ? 0 : 8))
        }
        .scrollIndicators(.hidden)
        .task {
            if store.overview == nil { await store.loadOverview() }
            if store.coach == nil { await store.loadCoach("daily") }
            withAnimation(reduceMotion ? nil : .easeOut(duration: 0.34)) { appeared = true }
        }
    }

    private var greeting: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text("PERFORMANCE PRIVÉE")
                .font(.trackLabel(9)).tracking(2).foregroundStyle(Brand.secondaryText)
            Text("Bonjour \(store.member?.firstName.uppercased() ?? "TRADER")")
                .font(.trackDisplay(32))
                .tracking(-0.85)
            Text(store.entitlements.source == "ACADEMY_INCLUDED" ? "BECTANSE MEMBER · JOURNAL INCLUS" : "BECTANSE TRACK · ACCÈS ACTIF")
                .font(.trackLabel(8)).tracking(1.25).foregroundStyle(Brand.orange)
        }
    }

    private var emptyAccount: some View {
        VStack(alignment: .leading, spacing: 18) {
            Image(systemName: "link.badge.plus")
                .font(.system(size: 28, weight: .semibold))
                .foregroundStyle(Brand.orange)
            Text("Connectez votre premier compte MT5")
                .font(.trackDisplay(27))
                .tracking(-0.65)
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
                        .font(TrackType.body(14, weight: .semibold))
                    Text(accountStatus)
                        .font(.caption)
                        .foregroundStyle(Brand.secondaryText)
                        .lineLimit(1)
                }
                Spacer()
                Button {
                    Tactile.impact()
                    Task { await store.syncNow() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 16, weight: .semibold))
                        .frame(width: 44, height: 44)
                        .background(Brand.surfaceRaised)
                        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(Brand.lineStrong, lineWidth: 0.75))
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                }
                .buttonStyle(TactileCardButtonStyle())
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
                    .font(TrackType.body(14, weight: .medium))
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
            LoadingPanel()
        }
    }

    @ViewBuilder private var equityChart: some View {
        if let equity = store.overview?.equity {
            VStack(alignment: .leading, spacing: 15) {
                HStack(alignment: .top, spacing: 12) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("COURBE DE PERFORMANCE").font(.trackLabel(9)).tracking(1.45).foregroundStyle(Brand.secondaryText)
                        Text("P&L cumulé").font(TrackType.heading(20)).tracking(-0.35)
                    }
                    Spacer()
                    VStack(alignment: .trailing, spacing: 4) {
                        Text("\(equity.tradeCount) positions")
                            .font(.caption)
                            .foregroundStyle(Brand.secondaryText)
                        if let point = focusedPoint(in: equity.points) {
                            Text(TrackFormat.money(point.cumulativePnl, currency: equity.currency, signed: true))
                                .font(.trackMetric(14))
                                .foregroundStyle(point.cumulativePnl >= 0 ? Brand.positive : Brand.negative)
                                .contentTransition(.numericText())
                            Text(chartPointLabel(point))
                                .font(.trackLabel(7.5))
                                .foregroundStyle(Brand.secondaryText)
                                .lineLimit(1)
                        }
                    }
                }
                if equity.points.count > 1 {
                    Chart {
                        ForEach(equity.points) { point in
                            AreaMark(
                                x: .value("Position", point.tradeIndex ?? 0),
                                y: .value("P&L", point.cumulativePnl)
                            )
                            .interpolationMethod(.monotone)
                            .foregroundStyle(
                                LinearGradient(
                                    colors: [Brand.orange.opacity(0.24), Brand.orange.opacity(0.035), .clear],
                                    startPoint: .top,
                                    endPoint: .bottom
                                )
                            )
                            LineMark(
                                x: .value("Position", point.tradeIndex ?? 0),
                                y: .value("P&L", point.cumulativePnl)
                            )
                            .interpolationMethod(.monotone)
                            .foregroundStyle(Brand.orange)
                            .lineStyle(StrokeStyle(lineWidth: 2.25, lineCap: .round, lineJoin: .round))
                        }
                        if let selected = selectedPoint(in: equity.points) {
                            RuleMark(x: .value("Position", selected.tradeIndex ?? 0))
                                .foregroundStyle(Brand.primaryText.opacity(0.46))
                                .lineStyle(StrokeStyle(lineWidth: 0.8, dash: [3, 3]))
                            PointMark(
                                x: .value("Position", selected.tradeIndex ?? 0),
                                y: .value("P&L", selected.cumulativePnl)
                            )
                            .symbolSize(52)
                            .foregroundStyle(Brand.orange)
                        }
                    }
                    .chartXScale(domain: chartXDomain(equity.points))
                    .chartYScale(domain: chartYDomain(equity.points))
                    .chartXAxis {
                        AxisMarks(position: .bottom, values: .automatic(desiredCount: 3)) { value in
                            AxisTick(stroke: StrokeStyle(lineWidth: 0.6)).foregroundStyle(Brand.lineStrong)
                            AxisValueLabel {
                                if let index = value.as(Int.self) {
                                    Text("#\(index + 1)")
                                        .font(.trackLabel(7.5))
                                        .foregroundStyle(Brand.secondaryText)
                                }
                            }
                        }
                    }
                    .chartYAxis {
                        AxisMarks(position: .trailing, values: .automatic(desiredCount: 4)) { value in
                            AxisGridLine(stroke: StrokeStyle(lineWidth: 0.55, dash: [3, 5])).foregroundStyle(Brand.lineStrong)
                            AxisTick(stroke: StrokeStyle(lineWidth: 0.6)).foregroundStyle(Brand.lineStrong)
                            AxisValueLabel {
                                if let amount = value.as(Double.self) {
                                    Text(chartAxisLabel(amount))
                                        .font(.trackMetric(8.5))
                                        .foregroundStyle(Brand.secondaryText)
                                }
                            }
                        }
                    }
                    .chartPlotStyle { plot in
                        plot
                            .background(Brand.backgroundElevated.opacity(0.34))
                            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                    }
                    .chartOverlay { proxy in
                        GeometryReader { geometry in
                            Rectangle().fill(.clear).contentShape(Rectangle())
                                .simultaneousGesture(
                                    DragGesture(minimumDistance: 0)
                                        .onChanged { value in
                                            guard abs(value.translation.height) <= abs(value.translation.width) + 10 else { return }
                                            guard let plotFrame = proxy.plotFrame else { return }
                                            let frame = geometry[plotFrame]
                                            let x = value.location.x - frame.origin.x
                                            guard x >= 0, x <= frame.width,
                                                  let index: Int = proxy.value(atX: x)
                                            else { return }
                                            let nearest = selectedPoint(to: index, in: equity.points)?.tradeIndex
                                            if nearest != selectedTradeIndex {
                                                Tactile.selection()
                                                withAnimation(Brand.Motion.quick) { selectedTradeIndex = nearest }
                                            }
                                        }
                                )
                        }
                    }
                    .frame(height: 228)
                    .mask(alignment: .leading) {
                        Rectangle()
                            .scaleEffect(x: appeared || reduceMotion ? 1 : 0.02, anchor: .leading)
                    }
                    .animation(reduceMotion ? nil : .easeOut(duration: 0.72), value: appeared)
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
                                .font(.trackMetric(10))
                                .foregroundStyle(day.netPnl >= 0 ? Brand.positive : Brand.negative)
                                .minimumScaleFactor(0.6)
                                .lineLimit(1)
                        }
                        .frame(maxWidth: .infinity, minHeight: 66)
                        .background(day.netPnl == 0 ? Brand.raised.opacity(0.35) : (day.netPnl > 0 ? Brand.positive.opacity(0.07) : Brand.negative.opacity(0.07)))
                        .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
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
                .font(.trackDisplay(24))
                .tracking(-0.5)
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

    private func selectedPoint(in points: [EquityPoint]) -> EquityPoint? {
        guard let selectedTradeIndex else { return nil }
        return selectedPoint(to: selectedTradeIndex, in: points)
    }

    private func focusedPoint(in points: [EquityPoint]) -> EquityPoint? {
        selectedPoint(in: points) ?? points.last
    }

    private func selectedPoint(to index: Int, in points: [EquityPoint]) -> EquityPoint? {
        points.min { abs(($0.tradeIndex ?? 0) - index) < abs(($1.tradeIndex ?? 0) - index) }
    }

    private func chartXDomain(_ points: [EquityPoint]) -> ClosedRange<Int> {
        let values = points.map { $0.tradeIndex ?? 0 }
        let lower = values.min() ?? 0
        let upper = values.max() ?? max(1, lower + 1)
        return lower...max(lower + 1, upper)
    }

    private func chartYDomain(_ points: [EquityPoint]) -> ClosedRange<Double> {
        let values = points.map(\.cumulativePnl)
        let minimum = values.min() ?? 0
        let maximum = values.max() ?? 1
        let span = max(1, maximum - minimum)
        let padding = max(span * 0.12, max(abs(minimum), abs(maximum)) * 0.025, 1)
        return (minimum - padding)...(maximum + padding)
    }

    private func chartAxisLabel(_ value: Double) -> String {
        let magnitude = abs(value)
        if magnitude >= 1_000_000 { return String(format: "%.1f M", value / 1_000_000) }
        if magnitude >= 10_000 { return String(format: "%.0f k", value / 1_000) }
        if magnitude >= 1_000 { return String(format: "%.1f k", value / 1_000) }
        return String(format: "%.0f", value)
    }

    private func chartPointLabel(_ point: EquityPoint) -> String {
        guard let raw = point.at ?? point.date else { return "POSITION #\((point.tradeIndex ?? 0) + 1)" }
        if let date = ISO8601DateFormatter().date(from: raw) {
            return date.formatted(.dateTime.day().month(.abbreviated).hour().minute().locale(Locale(identifier: "fr_FR"))).uppercased()
        }
        let parser = DateFormatter()
        parser.locale = Locale(identifier: "en_US_POSIX")
        parser.dateFormat = "yyyy-MM-dd"
        guard let date = parser.date(from: raw) else { return raw }
        return date.formatted(.dateTime.day().month(.abbreviated).locale(Locale(identifier: "fr_FR"))).uppercased()
    }
}
