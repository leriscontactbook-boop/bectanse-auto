import SwiftUI

struct CalendarView: View {
    @EnvironmentObject private var store: AppStore
    private let columns = Array(repeating: GridItem(.flexible(), spacing: 3), count: 7)
    private let weekdayLabels = ["L", "M", "M", "J", "V", "S", "D"]

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 18) {
                monthHeader
                if let payload = store.calendar {
                    summary(payload)
                    calendarGrid(payload)
                    if payload.dataQuality?.complete == false {
                        Label(
                            "Certaines positions sont encore en rapprochement. Le P&L affiché vient bien des sorties MT5 vérifiées.",
                            systemImage: "checkmark.shield"
                        )
                        .font(.caption)
                        .foregroundStyle(Brand.secondaryText)
                        .trackCard(padding: 14)
                    }
                } else {
                    ProgressView().tint(Brand.orange).frame(maxWidth: .infinity).padding(50)
                }
            }
            .padding(.horizontal, 12)
            .padding(.top, 20)
            .padding(.bottom, 18)
        }
        .scrollIndicators(.hidden)
        .task { if store.calendar == nil { await store.loadOverview() } }
    }

    private var monthHeader: some View {
        HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: 5) {
                Text("JOUR APRÈS JOUR").font(.trackLabel(9)).tracking(2).foregroundStyle(Brand.secondaryText)
                Text(monthTitle).font(.trackDisplay(36))
            }
            Spacer()
            HStack(spacing: 2) {
                monthButton("chevron.left", delta: -1)
                monthButton("circle", delta: 0)
                monthButton("chevron.right", delta: 1)
            }
            .background(Brand.surface)
            .clipShape(RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(Brand.line))
        }
    }

    private func monthButton(_ icon: String, delta: Int) -> some View {
        Button {
            Task {
                if delta == 0 {
                    let current = Date().formatted(.iso8601.year().month())
                    await store.loadCalendar(month: String(current.prefix(7)))
                } else {
                    await store.moveMonth(delta)
                }
            }
        } label: {
            Image(systemName: icon)
                .font(.system(size: icon == "circle" ? 7 : 12, weight: .bold))
                .frame(width: 40, height: 42)
                .foregroundStyle(icon == "circle" ? Brand.orange : Brand.secondaryText)
        }
        .accessibilityLabel(delta == -1 ? "Mois précédent" : delta == 1 ? "Mois suivant" : "Mois actuel")
    }

    private var monthTitle: String {
        guard let date = Self.monthParser.date(from: store.month) else { return store.month.uppercased() }
        return date.formatted(.dateTime.month(.wide).year().locale(Locale(identifier: "fr_FR"))).uppercased()
    }

    private func summary(_ payload: CalendarPayload) -> some View {
        let s = payload.summary
        return LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible()), GridItem(.flexible())], spacing: 8) {
            CalendarMetric(label: "P&L DU MOIS", value: TrackFormat.money(s.netPnl, currency: payload.currency, signed: true), tone: s.netPnl >= 0 ? Brand.positive : Brand.negative)
            CalendarMetric(label: "RETURN", value: TrackFormat.percent(s.returnPct, signed: true))
            CalendarMetric(label: "POSITIONS", value: "\(s.trades)")
            CalendarMetric(label: "WIN RATE", value: TrackFormat.percent(s.winRate))
            CalendarMetric(label: "JOURS TRADÉS", value: "\(s.tradingDays)")
            CalendarMetric(label: "GAINS / PERTES", value: "\(s.wins) / \(s.losses)")
        }
    }

    private func calendarGrid(_ payload: CalendarPayload) -> some View {
        let cells = buildCells(payload)
        return VStack(spacing: 3) {
            LazyVGrid(columns: columns, spacing: 3) {
                ForEach(Array(weekdayLabels.enumerated()), id: \.offset) { _, label in
                    Text(label).font(.trackLabel(8)).foregroundStyle(Brand.secondaryText).frame(height: 28)
                }
            }
            LazyVGrid(columns: columns, spacing: 3) {
                ForEach(Array(cells.enumerated()), id: \.offset) { _, cell in
                    CalendarCell(cell: cell, currency: payload.currency)
                }
            }
        }
        .padding(7)
        .background(Brand.surface)
        .clipShape(RoundedRectangle(cornerRadius: 21))
        .overlay(RoundedRectangle(cornerRadius: 21).stroke(Brand.line))
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
            result.append(DayCell(day: day, data: map[key]))
        }
        while result.count % 7 != 0 { result.append(.empty) }
        return result
    }

    private static let monthParser: DateFormatter = {
        let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = "yyyy-MM"; return f
    }()
    private static let dayParser: DateFormatter = {
        let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = "yyyy-MM-dd"; return f
    }()
}
private struct CalendarMetric: View {
    let label: String
    let value: String
    var tone: Color = Brand.primaryText
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(label).font(.trackLabel(7)).tracking(0.8).foregroundStyle(Brand.secondaryText).lineLimit(1)
            Text(value).font(.trackDisplay(20)).foregroundStyle(tone).lineLimit(1).minimumScaleFactor(0.55)
        }
        .frame(maxWidth: .infinity, minHeight: 69, alignment: .leading)
        .trackCard(padding: 11)
    }
}

private struct DayCell {
    let day: Int?
    let data: TradingDay?
    static let empty = DayCell(day: nil, data: nil)
}

private struct CalendarCell: View {
    let cell: DayCell
    let currency: String
    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            if let day = cell.day {
                Text("\(day)")
                    .font(.system(size: 9, weight: .bold, design: .rounded))
                    .foregroundStyle(Brand.secondaryText)
                Spacer(minLength: 1)
                if let data = cell.data, data.trades > 0 {
                    Text(compact(data.netPnl))
                        .font(.system(size: 8, weight: .bold, design: .rounded))
                        .foregroundStyle(data.netPnl >= 0 ? Brand.positive : Brand.negative)
                        .lineLimit(1).minimumScaleFactor(0.55)
                    Text("\(data.trades) pos.")
                        .font(.system(size: 6.5, weight: .medium, design: .rounded))
                        .foregroundStyle(Brand.secondaryText)
                        .lineLimit(1)
                }
            }
        }
        .padding(7)
        .frame(maxWidth: .infinity, minHeight: 68, alignment: .leading)
        .background(background)
        .clipShape(RoundedRectangle(cornerRadius: 9))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText)
    }

    private var background: Color {
        guard let data = cell.data, data.trades > 0 else { return Brand.raised.opacity(cell.day == nil ? 0.15 : 0.42) }
        return (data.netPnl >= 0 ? Brand.positive : Brand.negative).opacity(0.105)
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
