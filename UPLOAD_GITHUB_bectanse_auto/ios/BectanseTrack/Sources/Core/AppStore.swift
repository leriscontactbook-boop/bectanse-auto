import Foundation

@MainActor
final class AppStore: ObservableObject {
    enum Phase: Equatable { case launching, signedOut, ready }

    @Published var phase: Phase = .launching
    @Published var member: MemberProfile?
    @Published var profile = JournalProfile(timezone: "Europe/Paris")
    @Published var entitlements = Entitlements.locked
    @Published var accounts: [TradingAccount] = []
    @Published var selectedAccountID: Int?
    @Published var overview: OverviewResponse?
    @Published var calendar: CalendarPayload?
    @Published var trades: [Trade] = []
    @Published var tradeCurrency = "EUR"
    @Published var analytics: AnalyticsResponse?
    @Published var coach: CoachResponse?
    @Published var isLoadingCoach = false
    @Published var coachLoadError: String?
    @Published var brokers: [Broker] = []
    @Published var selectedTab: AppTab = .overview
    @Published var isLoading = false
    @Published var isRefreshing = false
    @Published var alertMessage: String?

    private let api = APIClient.shared
    private var bootstrapped = false
    private var coachRequestID: UUID?

    var activeAccount: TradingAccount? {
        accounts.first(where: { $0.id == selectedAccountID }) ?? accounts.first
    }

    var scope: String { activeAccount.map { String($0.id) } ?? "all" }
    var month: String { calendar?.month ?? Self.monthFormatter.string(from: Date()) }

    func bootstrap() async {
        guard !bootstrapped else { return }
        bootstrapped = true
#if DEBUG
        if ProcessInfo.processInfo.arguments.contains("-ui-testing") {
            loadUITestData()
            if let tabName = ProcessInfo.processInfo.arguments.first(where: { $0.hasPrefix("-ui-tab=") }),
               let tab = AppTab(rawValue: String(tabName.dropFirst("-ui-tab=".count))) {
                selectedTab = tab
            }
            try? await Task.sleep(for: .milliseconds(900))
            phase = .ready
            return
        }
#endif
        do {
            let response: MobileSessionResponse = try await api.get("api/mobile/session")
            apply(response)
            if member != nil {
                if entitlements.allowed { await loadOverview() }
            } else {
                phase = .signedOut
            }
        } catch {
            if let code = CredentialVault.read() {
                do {
                    let response: MobileSessionResponse = try await api.post(
                        "api/mobile/auth/code", body: CodeLoginBody(code: code)
                    )
                    apply(response)
                    if entitlements.allowed { await loadOverview() }
                } catch {
                    CredentialVault.clear()
                    phase = .signedOut
                }
            } else {
                phase = .signedOut
            }
        }
        if member != nil { phase = .ready }
    }

    func login(code: String) async {
        await performAuth {
            let clean = code.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
            let response: MobileSessionResponse = try await self.api.post(
                "api/mobile/auth/code", body: CodeLoginBody(code: clean)
            )
            CredentialVault.save(clean)
            return response
        }
    }

    private func performAuth(_ action: () async throws -> MobileSessionResponse) async {
        isLoading = true
        defer { isLoading = false }
        do {
            let response = try await action()
            apply(response)
            phase = .ready
            Tactile.success()
            if entitlements.allowed { await loadOverview() }
        } catch {
            alertMessage = error.localizedDescription
        }
    }

    func logout() async {
        let _: MessageResponse? = try? await api.post("api/mobile/logout", body: EmptyRequest())
        CredentialVault.clear()
        member = nil
        accounts = []
        overview = nil
        phase = .signedOut
    }

    func refreshSession() async {
        do {
            let response: MobileSessionResponse = try await api.get("api/mobile/session")
            apply(response)
            if entitlements.allowed { await loadOverview() }
        } catch {
            alertMessage = error.localizedDescription
        }
    }

    func loadOverview() async {
        guard !accounts.isEmpty, entitlements.allowed else { return }
        isRefreshing = true
        defer { isRefreshing = false }
        do {
            let response: OverviewResponse = try await api.get(
                "api/trading/overview",
                query: contextQuery + [URLQueryItem(name: "month", value: month)]
            )
            overview = response
            calendar = response.calendar
        } catch {
            alertMessage = error.localizedDescription
        }
    }

    func loadTrades() async {
        guard !accounts.isEmpty else { return }
        do {
            let response: TradesResponse = try await api.get(
                "api/trading/trades",
                query: contextQuery + [URLQueryItem(name: "limit", value: "250")]
            )
            trades = response.trades
            tradeCurrency = response.currency
        } catch {
            alertMessage = error.localizedDescription
        }
    }

    func loadCalendar(month: String) async {
        guard !accounts.isEmpty else { return }
        do {
            let response: CalendarResponse = try await api.get(
                "api/trading/calendar",
                query: contextQuery + [URLQueryItem(name: "month", value: month)]
            )
            calendar = response.payload
        } catch {
            alertMessage = error.localizedDescription
        }
    }

    func tradingDay(_ date: String) async throws -> TradingDayResponse {
        guard !accounts.isEmpty, entitlements.allowed else {
            throw APIError(statusCode: 403, message: "Aucun compte de trading actif.")
        }
#if DEBUG
        if ProcessInfo.processInfo.arguments.contains("-ui-testing") {
            return uiTestTradingDay(date)
        }
#endif
        return try await api.get(
            "api/trading/days/\(date)",
            query: contextQuery
        )
    }

    func moveMonth(_ delta: Int) async {
        guard let current = Self.monthFormatter.date(from: month),
              let target = Calendar(identifier: .gregorian).date(byAdding: .month, value: delta, to: current)
        else { return }
        await loadCalendar(month: Self.monthFormatter.string(from: target))
    }

    func loadAnalytics() async {
        guard entitlements.advancedAnalytics, !accounts.isEmpty else { return }
        do {
            analytics = try await api.get("api/trading/analytics", query: contextQuery)
        } catch {
            alertMessage = error.localizedDescription
        }
    }

    func loadCoach(_ review: String = "daily") async {
        guard entitlements.features["coach.\(review)"] == true, !accounts.isEmpty else { return }
        let requestID = UUID()
        coachRequestID = requestID
        isLoadingCoach = true
        coachLoadError = nil
        defer {
            if coachRequestID == requestID { isLoadingCoach = false }
        }
        do {
#if DEBUG
            if ProcessInfo.processInfo.arguments.contains("-ui-testing") {
                coach = uiTestCoach(review)
                return
            }
#endif
            let response: CoachResponse = try await api.get(
                "api/trading/coach/\(review)", query: contextQuery
            )
            guard coachRequestID == requestID, !Task.isCancelled else { return }
            coach = response
        } catch is CancellationError {
            return
        } catch {
            guard coachRequestID == requestID else { return }
            coachLoadError = error.localizedDescription
            alertMessage = error.localizedDescription
        }
    }

    func loadBrokers() async {
        do {
            let response: BrokerCatalogResponse = try await api.get("api/trading/brokers")
            brokers = response.brokers
        } catch {
            alertMessage = error.localizedDescription
        }
    }

    func connectAccount(_ body: ConnectAccountBody) async -> Bool {
        isLoading = true
        defer { isLoading = false }
        do {
            let response: ConnectAccountResponse = try await api.post(
                "api/trading/accounts/connect", body: body
            )
            accounts.append(response.account)
            selectedAccountID = response.account.id
            Tactile.success()
            return true
        } catch {
            alertMessage = error.localizedDescription
            return false
        }
    }

    func syncNow() async {
        guard let account = activeAccount else { return }
        do {
            let response: SyncResponse = try await api.post(
                "api/trading/accounts/\(account.id)/sync", body: EmptyRequest()
            )
            alertMessage = response.queued
                ? "Synchronisation MT5 lancée. Les données vont s’actualiser automatiquement."
                : "Une synchronisation récente est déjà en cours."
            try? await Task.sleep(for: .seconds(2))
            await reloadAccounts()
            await loadOverview()
        } catch {
            alertMessage = error.localizedDescription
        }
    }

    func selectAccount(_ id: Int) async {
        selectedAccountID = id
        overview = nil
        calendar = nil
        trades = []
        analytics = nil
        coach = nil
        coachRequestID = nil
        isLoadingCoach = false
        coachLoadError = nil
        await loadOverview()
    }

    func reloadAccounts() async {
        do {
            let response: AccountsResponse = try await api.get("api/trading/accounts")
            accounts = response.accounts
            entitlements = response.entitlements
            if activeAccount == nil { selectedAccountID = accounts.first?.id }
        } catch {
            alertMessage = error.localizedDescription
        }
    }

    private var contextQuery: [URLQueryItem] {
        [
            URLQueryItem(name: "account_id", value: scope),
            URLQueryItem(name: "timezone", value: profile.timezone),
        ]
    }

    private func apply(_ response: MobileSessionResponse) {
        guard response.authenticated, let member = response.member else { return }
        self.member = member
        profile = response.profile ?? JournalProfile(timezone: "Europe/Paris")
        entitlements = response.entitlements ?? .locked
        accounts = response.accounts ?? []
        if activeAccount == nil { selectedAccountID = accounts.first?.id }
    }

    private static let monthFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM"
        return formatter
    }()
}

struct EmptyRequest: Encodable {}

enum AppTab: String, CaseIterable, Identifiable {
    case overview, trades, calendar, analytics, coach
    var id: String { rawValue }

    var title: String {
        switch self {
        case .overview: "Vue"
        case .trades: "Trades"
        case .calendar: "Calendrier"
        case .analytics: "Analytics"
        case .coach: "Coach"
        }
    }

    var icon: String {
        switch self {
        case .overview: "chart.bar.fill"
        case .trades: "list.bullet.rectangle"
        case .calendar: "calendar"
        case .analytics: "chart.xyaxis.line"
        case .coach: "sparkles"
        }
    }
}
