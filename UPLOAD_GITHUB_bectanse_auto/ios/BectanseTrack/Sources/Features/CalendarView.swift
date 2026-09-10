import SwiftUI

struct CalendarView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var selectedDate: String?
    @State private var presentedDay: CalendarDaySelection?
    @State private var appeared = false

    private let columns = Array(repeating: GridItem(.flexible(), spacing: 4), count: 7)
    private let weekdayLabels = ["L", "M", "M", "J", "V", "S", "D"]

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                monthHeader
                if let payload = store.calendar {
                    summary(payload)
                    calendarGrid(payload)
                    if let selected = payload.days.first(where: { $0.date == selectedDate }) {
                        selectedDayPanel(selected, currency: payload.currency)
                            .transition(.opacity.combined(with: .move(edge: .top)))
                    }
                    if payload.dataQuality?.complete == false {
                        Label(
                            "Certaines positions sont encore en rapprochement. Le P&L affiché vient des sorties MT5 vérifiées.",
                            systemImage: "checkmark.shield"
                        )
                        .font(TrackType.body(12))
                        .foregroundStyle(Brand.secondaryText)
                        .trackCard(padding: 14)
                    }
                } else {
                    LoadingPanel()
                }
            }
            .padding(.horizontal, 14)
            .padding(.top, 18)
            .padding(.bottom, 18)
            .opacity(appeared ? 1 : 0.01)
            .offset(y: appeared ? 0 : (reduceMotion ? 0 : 8))
        }
        .scrollIndicators(.hidden)
        .task {
            if store.calendar == nil { await store.loadOverview() }
            selectTodayIfAvailable()
            withAnimation(reduceMotion ? nil : .easeOut(duration: 0.34)) { appeared = true }
        }
        .onChange(of: store.calendar?.month) { _, _ in selectTodayIfAvailable() }
        .fullScreenCover(item: $presentedDay) { selection in
            TradingDayHistoryView(date: selection.date)
                .environmentObject(store)
        }
    }

    private var monthHeader: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .center, spacing: 16) {
                monthIdentity
                Spacer(minLength: 8)
                monthNavigation
            }
            VStack(alignment: .leading, spacing: 14) {
                monthIdentity
                monthNavigation
            }
        }
    }

    private var monthIdentity: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text("JOUR APRÈS JOUR")
                .font(.trackLabel(9))
                .tracking(1.7)
                .foregroundStyle(Brand.secondaryText)
            Text(monthTitle)
                .font(.trackDisplay(30))
                .tracking(-0.9)
                .lineLimit(1)
                .minimumScaleFactor(0.75)
        }
    }

    private var monthNavigation: some View {
        HStack(spacing: 0) {
            monthButton("chevron.left", delta: -1)
            Rectangle().fill(Brand.line).frame(width: 1, height: 22)
            monthButton("circle.fill", delta: 0)
            Rectangle().fill(Brand.line).frame(width: 1, height: 22)
            monthButton("chevron.right", delta: 1)
        }
        .background(Brand.surface)
        .clipShape(RoundedRectangle(cornerRadius: Brand.Radius.control, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Brand.Radius.control, style: .continuous).stroke(Brand.lineStrong, lineWidth: 0.75))
    }

    private func monthButton(_ icon: String, delta: Int) -> some View {
        Button {
            Tactile.selection()
            selectedDate = nil
            Task {
                if delta == 0 {
                    await store.loadCalendar(month: Self.monthParser.string(from: Date()))
                } else {
                    await store.moveMonth(delta)
                }
            }
        } label: {
            Image(systemName: icon)
                .font(.system(size: delta == 0 ? 6 : 11, weight: .semibold))
                .frame(width: 42, height: 42)
                .foregroundStyle(delta == 0 ? Brand.orange : Brand.secondaryText)
                .contentShape(Rectangle())
        }
        .buttonStyle(TactileCardButtonStyle())
        .accessibilityLabel(delta == -1 ? "Mois précédent" : delta == 1 ? "Mois suivant" : "Mois actuel")
    }

    private var monthTitle: String {
        guard let date = Self.monthParser.date(from: store.month) else { return store.month.uppercased() }
        return date.formatted(.dateTime.month(.wide).year().locale(Locale(identifier: "fr_FR"))).uppercased()
    }

    private func summary(_ payload: CalendarPayload) -> some View {
        let summary = payload.summary
        return VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 7) {
                Text("P&L DU MOIS")
                    .font(.trackLabel(8.5)).tracking(1.2).foregroundStyle(Brand.secondaryText)
                Text(TrackFormat.money(summary.netPnl, currency: payload.currency, signed: true))
                    .font(.trackMetric(32))
                    .tracking(-1.0)
                    .foregroundStyle(summary.netPnl >= 0 ? Brand.positive : Brand.negative)
                    .lineLimit(1)
                    .minimumScaleFactor(0.62)
                    .contentTransition(.numericText())
            }
            .padding(16)

            Rectangle().fill(Brand.line).frame(height: 1)

            HStack(spacing: 0) {
                SummaryDatum(label: "RETURN", value: TrackFormat.percent(summary.returnPct, signed: true))
                verticalDivider
                SummaryDatum(label: "POSITIONS", value: "\(summary.trades)")
                verticalDivider
                SummaryDatum(label: "WIN RATE", value: TrackFormat.percent(summary.winRate))
            }
            .padding(.vertical, 14)

            Rectangle().fill(Brand.line).frame(height: 1)

            HStack(spacing: 0) {
                SummaryDatum(label: "JOURS TRADÉS", value: "\(summary.tradingDays)")
                verticalDivider
                SummaryDatum(label: "GAINS / PERTES", value: "\(summary.wins) / \(summary.losses)")
            }
            .padding(.vertical, 14)
        }
        .background {
            ZStack {
                Brand.surface
                LinearGradient(colors: [Color.white.opacity(0.022), .clear], startPoint: .topLeading, endPoint: .bottomTrailing)
            }
        }
        .overlay(RoundedRectangle(cornerRadius: Brand.Radius.card, style: .continuous).stroke(Brand.lineStrong, lineWidth: 0.75))
        .clipShape(RoundedRectangle(cornerRadius: Brand.Radius.card, style: .continuous))
    }

    private var verticalDivider: some View {
        Rectangle().fill(Brand.line).frame(width: 1, height: 42)
    }

    private func calendarGrid(_ payload: CalendarPayload) -> some View {
        let cells = buildCells(payload)
        return VStack(spacing: 4) {
            LazyVGrid(columns: columns, spacing: 4) {
                ForEach(Array(weekdayLabels.enumerated()), id: \.offset) { _, label in
                    Text(label)
                        .font(.trackLabel(8))
                        .foregroundStyle(Brand.mutedText)
                        .frame(height: 28)
                }
            }
            LazyVGrid(columns: columns, spacing: 4) {
                ForEach(Array(cells.enumerated()), id: \.offset) { _, cell in
                    CalendarCell(
                        cell: cell,
                        currency: payload.currency,
                        selected: cell.date != nil && cell.date == selectedDate,
                        today: cell.date == Self.todayKey
                    ) {
                        guard let date = cell.date else { return }
                        Tactile.selection()
                        selectedDate = date
                        presentedDay = CalendarDaySelection(date: date)
                    }
                }
            }
        }
        .padding(8)
        .background(Brand.backgroundElevated)
        .clipShape(RoundedRectangle(cornerRadius: Brand.Radius.card, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Brand.Radius.card, style: .continuous).stroke(Brand.lineStrong, lineWidth: 0.75))
    }

    private func selectedDayPanel(_ day: TradingDay, currency: String) -> some View {
        Button {
            presentedDay = CalendarDaySelection(date: day.date)
        } label: {
            HStack(spacing: 14) {
                VStack(alignment: .leading, spacing: 5) {
                    Text(dayTitle(day.date))
                        .font(.trackLabel(9))
                        .tracking(1.2)
                        .foregroundStyle(Brand.secondaryText)
                    Text(TrackFormat.money(day.netPnl, currency: currency, signed: true))
                        .font(.trackMetric(24))
                        .foregroundStyle(day.netPnl >= 0 ? Brand.positive : Brand.negative)
                        .contentTransition(.numericText())
                }
                Spacer()
                DayFact(label: "POSITIONS", value: "\(day.trades)")
                DayFact(label: "G / P", value: "\(day.wins) / \(day.losses)")
                Image(systemName: "chevron.right")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Brand.orange)
            }
            .trackCard(padding: 16, highlighted: true)
        }
        .buttonStyle(TactileCardButtonStyle())
        .accessibilityLabel("Voir les positions du \(dayTitle(day.date))")
    }

    private func buildCells(_ payload: CalendarPayload) -> [DayCell] {
        guard let first = Self.dayParser.date(from: payload.month + "-01"),
              let range = Calendar(identifier: .gregorian).range(of: .day, in: .month, for: first)
        else { return [] }
        let weekday = Calendar(identifier: .gregorian).component(.weekday, from: first)
        let leading = (weekday + 5) % 7
        let map = Dictionary(uniqueKeysWithValues: payload.days.map { ($0.date, $0) })
        var result = Array(repeating: DayCell.empty, count: leading)
        for day in range {
            let key = String(format: "%@-%02d", payload.month, day)
            result.append(DayCell(day: day, date: key, data: map[key]))
        }
        while result.count % 7 != 0 { result.append(.empty) }
        return result
    }

    private func selectTodayIfAvailable() {
        guard selectedDate == nil, store.calendar?.days.contains(where: { $0.date == Self.todayKey }) == true else { return }
        selectedDate = Self.todayKey
    }

    private func dayTitle(_ raw: String) -> String {
        guard let date = Self.dayParser.date(from: raw) else { return raw }
        return date.formatted(.dateTime.weekday(.wide).day().month(.wide).locale(Locale(identifier: "fr_FR"))).uppercased()
    }

    private static let monthParser: DateFormatter = {
        let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = "yyyy-MM"; return f
    }()
    fileprivate static let dayParser: DateFormatter = {
        let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = "yyyy-MM-dd"; return f
    }()
    private static let todayKey: String = dayParser.string(from: Date())
}

private struct SummaryDatum: View {
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(.trackLabel(7.5))
                .tracking(0.85)
                .foregroundStyle(Brand.mutedText)
                .lineLimit(1)
            Text(value)
                .font(.trackMetric(17))
                .tracking(-0.4)
                .foregroundStyle(Brand.primaryText)
                .lineLimit(1)
                .minimumScaleFactor(0.62)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 14)
    }
}

private struct DayFact: View {
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .trailing, spacing: 4) {
            Text(label).font(.trackLabel(7)).tracking(0.8).foregroundStyle(Brand.mutedText)
            Text(value).font(.trackMetric(14)).foregroundStyle(Brand.primaryText)
        }
    }
}

private struct DayCell {
    let day: Int?
    let date: String?
    let data: TradingDay?
    static let empty = DayCell(day: nil, date: nil, data: nil)
}

private struct CalendarDaySelection: Identifiable {
    let date: String
    var id: String { date }
}

private struct CalendarCell: View {
    let cell: DayCell
    let currency: String
    let selected: Bool
    let today: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 4) {
                if let day = cell.day {
                    HStack {
                        Text("\(day)")
                            .font(.trackLabel(8.5))
                            .foregroundStyle(today || selected ? Brand.primaryText : Brand.secondaryText)
                        Spacer(minLength: 0)
                        if today { Circle().fill(Brand.orange).frame(width: 4, height: 4) }
                    }
                    Spacer(minLength: 1)
                    if let data = cell.data, data.trades > 0 {
                        Text(compact(data.netPnl))
                            .font(.trackMetric(8))
                            .foregroundStyle(data.netPnl >= 0 ? Brand.positive : Brand.negative)
                            .lineLimit(1)
                            .minimumScaleFactor(0.52)
                        Text("\(data.trades) pos.")
                            .font(.trackLabel(6.5))
                            .foregroundStyle(Brand.mutedText)
                            .lineLimit(1)
                    }
                }
            }
            .padding(7)
            .frame(maxWidth: .infinity, minHeight: 66, alignment: .leading)
            .background(background)
            .overlay {
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .stroke(selected ? Brand.orange : Brand.line.opacity(0.55), lineWidth: selected ? 1.25 : 0.6)
            }
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            .shadow(color: selected ? Brand.orange.opacity(0.14) : .clear, radius: 8)
        }
        .buttonStyle(TactileCardButtonStyle())
        .disabled(cell.date == nil)
        .accessibilityLabel(accessibilityText)
        .accessibilityHint("Ouvre l’historique des positions de cette journée")
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private var background: some ShapeStyle {
        guard let data = cell.data, data.trades > 0 else {
            return AnyShapeStyle(Brand.surface.opacity(cell.day == nil ? 0.18 : 0.58))
        }
        let tone = data.netPnl >= 0 ? Brand.positive : Brand.negative
        return AnyShapeStyle(
            LinearGradient(colors: [tone.opacity(0.14), tone.opacity(0.055)], startPoint: .topLeading, endPoint: .bottomTrailing)
        )
    }

    private func compact(_ value: Double) -> String {
        let sign = value > 0 ? "+" : ""
        if abs(value) >= 1000 { return sign + String(format: "%.1fk", value / 1000) }
        return sign + String(format: "%.0f", value)
    }

    private var accessibilityText: String {
        guard let day = cell.day else { return "Hors du mois" }
        guard let data = cell.data else { return "Jour \(day), aucune position" }
        return "Jour \(day), \(data.trades) positions, \(TrackFormat.money(data.netPnl, currency: currency, signed: true))"
    }
}

private struct TradingDayHistoryView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    let date: String

    @State private var response: TradingDayResponse?
    @State private var errorMessage: String?
    @State private var isLoading = true

    var body: some View {
        ZStack {
            Brand.background.ignoresSafeArea()
            VStack(spacing: 0) {
                header
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        if isLoading {
                            LoadingPanel()
                        } else if let response {
                            daySummary(response)
                            SectionHeading(
                                eyebrow: "Historique vérifié",
                                title: "Positions clôturées",
                                trailing: "\(response.trades.count)"
                            )
                            if response.trades.isEmpty {
                                EmptyPanel(
                                    title: "Aucune position clôturée",
                                    message: "Aucun résultat MT5 vérifié n’est enregistré pour cette journée."
                                )
                            } else {
                                ForEach(response.trades) { trade in
                                    TradeRow(trade: trade, currency: response.currency, showsPrices: true)
                                }
                            }
                        } else {
                            EmptyPanel(
                                title: "Historique indisponible",
                                message: errorMessage ?? "Les données de cette journée n’ont pas pu être chargées."
                            )
                            Button("Réessayer") { Task { await load() } }
                                .buttonStyle(PrimaryButtonStyle())
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.top, 18)
                    .padding(.bottom, 28)
                }
                .scrollIndicators(.hidden)
            }
        }
        .task(id: date) { await load() }
    }

    private var header: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text("JOURNAL DE LA JOURNÉE")
                    .font(.trackLabel(8))
                    .tracking(1.5)
                    .foregroundStyle(Brand.orange)
                Text(dayTitle)
                    .font(.trackDisplay(22))
                    .lineLimit(1)
                    .minimumScaleFactor(0.72)
            }
            Spacer()
            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 12, weight: .semibold))
                    .frame(width: 40, height: 40)
                    .foregroundStyle(Brand.primaryText)
                    .background(Brand.surface)
                    .clipShape(Circle())
                    .overlay(Circle().stroke(Brand.lineStrong, lineWidth: 0.75))
            }
            .buttonStyle(TactileCardButtonStyle())
            .accessibilityLabel("Fermer l’historique")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(Brand.backgroundElevated)
        .overlay(alignment: .bottom) { Rectangle().fill(Brand.line).frame(height: 1) }
    }

    private func daySummary(_ payload: TradingDayResponse) -> some View {
        let summary = payload.summary
        return VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 6) {
                Text("P&L NET")
                    .font(.trackLabel(8)).tracking(1.2).foregroundStyle(Brand.secondaryText)
                Text(TrackFormat.money(summary.netPnl, currency: payload.currency, signed: true))
                    .font(.trackMetric(34))
                    .foregroundStyle(summary.netPnl >= 0 ? Brand.positive : Brand.negative)
                    .lineLimit(1)
                    .minimumScaleFactor(0.65)
            }
            Rectangle().fill(Brand.line).frame(height: 1)
            HStack(spacing: 0) {
                SummaryDatum(label: "POSITIONS", value: "\(summary.trades)")
                Rectangle().fill(Brand.line).frame(width: 1, height: 42)
                SummaryDatum(label: "WIN RATE", value: TrackFormat.percent(summary.winRate))
                Rectangle().fill(Brand.line).frame(width: 1, height: 42)
                SummaryDatum(label: "VOLUME", value: summary.volume.formatted(.number.precision(.fractionLength(2))))
            }
            Rectangle().fill(Brand.line).frame(height: 1)
            HStack(spacing: 0) {
                SummaryDatum(label: "GAINS", value: "\(summary.wins)")
                Rectangle().fill(Brand.line).frame(width: 1, height: 42)
                SummaryDatum(label: "PERTES", value: "\(summary.losses)")
                Rectangle().fill(Brand.line).frame(width: 1, height: 42)
                SummaryDatum(label: "FRAIS", value: TrackFormat.money(summary.fees, currency: payload.currency))
            }
            if !summary.dataComplete {
                Label("Rapprochement MT5 encore en cours", systemImage: "clock")
                    .font(TrackType.body(12))
                    .foregroundStyle(Brand.secondaryText)
            }
        }
        .trackCard(padding: 16)
    }

    private var dayTitle: String {
        guard let parsed = CalendarView.dayParser.date(from: date) else { return date }
        return parsed.formatted(
            .dateTime.weekday(.wide).day().month(.wide).year().locale(Locale(identifier: "fr_FR"))
        ).uppercased()
    }

    @MainActor
    private func load() async {
        isLoading = true
        errorMessage = nil
        do {
            response = try await store.tradingDay(date)
        } catch is CancellationError {
            return
        } catch {
            response = nil
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }
}
