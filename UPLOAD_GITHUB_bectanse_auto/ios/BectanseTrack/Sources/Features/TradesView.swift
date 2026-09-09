import SwiftUI

struct TradesView: View {
    @EnvironmentObject private var store: AppStore
    @State private var search = ""
    @State private var filter: DirectionFilter = .all

    enum DirectionFilter: String, CaseIterable, Identifiable {
        case all = "Tous"
        case buy = "Buy"
        case sell = "Sell"
        var id: String { rawValue }
    }

    private var filtered: [Trade] {
        store.trades.filter { trade in
            let matchesSearch = search.isEmpty || trade.symbol.localizedCaseInsensitiveContains(search)
            let matchesDirection = filter == .all || trade.direction.caseInsensitiveCompare(filter.rawValue) == .orderedSame
            return matchesSearch && matchesDirection
        }
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 14) {
                SectionHeading(eyebrow: "Historique vérifié", title: "Mes trades", trailing: "\(store.trades.count) positions")
                HStack(spacing: 10) {
                    HStack {
                        Image(systemName: "magnifyingglass").foregroundStyle(Brand.secondaryText)
                        TextField("Rechercher un symbole", text: $search)
                            .textInputAutocapitalization(.characters)
                            .autocorrectionDisabled()
                    }
                    .trackField()
                    Menu {
                        ForEach(DirectionFilter.allCases) { item in
                            Button(item.rawValue) { filter = item }
                        }
                    } label: {
                        Image(systemName: "line.3.horizontal.decrease")
                            .frame(width: 52, height: 52)
                            .foregroundStyle(Brand.orange)
                            .background(Brand.surface)
                            .overlay(RoundedRectangle(cornerRadius: 16).stroke(Brand.line))
                            .clipShape(RoundedRectangle(cornerRadius: 16))
                    }
                }

                if filtered.isEmpty {
                    EmptyPanel(
                        title: store.trades.isEmpty ? "Aucun trade synchronisé" : "Aucun résultat",
                        message: store.trades.isEmpty
                            ? "Vos positions clôturées apparaîtront ici après la synchronisation MT5."
                            : "Modifiez votre recherche ou le filtre sélectionné."
                    )
                } else {
                    ForEach(filtered) { trade in TradeRow(trade: trade, currency: store.tradeCurrency) }
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 20)
            .padding(.bottom, 18)
        }
        .scrollDismissesKeyboard(.interactively)
        .scrollIndicators(.hidden)
        .task { if store.trades.isEmpty { await store.loadTrades() } }
    }
}
private struct TradeRow: View {
    let trade: Trade
    let currency: String
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                Text(trade.symbol).font(.headline)
                Text(trade.direction)
                    .font(.trackLabel(8))
                    .foregroundStyle(trade.direction == "BUY" ? Brand.positive : Brand.negative)
                    .padding(.horizontal, 8).padding(.vertical, 4)
                    .background((trade.direction == "BUY" ? Brand.positive : Brand.negative).opacity(0.09))
                    .clipShape(Capsule())
                Spacer()
                Text(TrackFormat.money(trade.netPnl, currency: currency, signed: true))
                    .font(.headline)
                    .foregroundStyle(trade.netPnl >= 0 ? Brand.positive : Brand.negative)
            }
            Rectangle().fill(Brand.line).frame(height: 1)
            HStack {
                detail("Clôture", TrackFormat.dateTime(trade.closedAt))
                Spacer()
                detail("Volume", trade.volume.formatted(.number.precision(.fractionLength(2))) + " lot")
                Spacer()
                detail("Durée", TrackFormat.duration(trade.durationSeconds))
            }
        }
        .trackCard(padding: 16)
    }

    private func detail(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label.uppercased()).font(.trackLabel(7)).tracking(1).foregroundStyle(Brand.secondaryText)
            Text(value).font(.caption.weight(.semibold)).lineLimit(1).minimumScaleFactor(0.7)
        }
    }
}
