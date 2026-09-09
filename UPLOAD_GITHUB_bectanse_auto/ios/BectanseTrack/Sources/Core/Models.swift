import Foundation

struct MobileSessionResponse: Decodable {
    let ok: Bool
    let authenticated: Bool
    let member: MemberProfile?
    let profile: JournalProfile?
    let entitlements: Entitlements?
    let accounts: [TradingAccount]?
    let recoveryCode: String?
    let error: String?
}

struct StoreKitContextResponse: Decodable {
    let ok: Bool
    let appAccountToken: String
    let productIds: [String]
}

struct StoreKitSyncResponse: Decodable {
    let ok: Bool
    let status: String
    let plan: String
    let expiresAt: String
    let session: MobileSessionResponse
}

struct StoreKitSyncBody: Encodable {
    let signedTransaction: String
}
struct MemberProfile: Decodable {
    let name: String
    let firstName: String
    let email: String
    let accessLevel: String
}

struct JournalProfile: Decodable {
    let timezone: String
}

struct Entitlements: Decodable {
    let allowed: Bool
    let source: String
    let plan: String
    let maxAccounts: Int
    let advancedAnalytics: Bool
    let multiAccount: Bool
    let features: [String: Bool]

    static let locked = Entitlements(
        allowed: false,
        source: "NONE",
        plan: "NONE",
        maxAccounts: 0,
        advancedAnalytics: false,
        multiAccount: false,
        features: [:]
    )
}

struct TradingAccount: Decodable, Identifiable, Hashable {
    let id: Int
    let provider: String
    let platform: String
    let displayName: String
    let loginMasked: String
    let broker: String
    let server: String
    let currency: String
    let accountType: String
    let balance: Double?
    let equity: Double?
    let accessMode: String
    let status: String
    let syncStatus: String
    let lastSyncAt: String?
    let lastSuccessfulSyncAt: String?
    let lastErrorCode: String?
    let lastErrorMessage: String?

    var title: String { displayName.isEmpty ? (broker.isEmpty ? "MetaTrader 5" : broker) : displayName }
    var isSynced: Bool { status == "SYNCED" }
}

struct AccountsResponse: Decodable {
    let ok: Bool
    let accounts: [TradingAccount]
    let entitlements: Entitlements
}

struct OverviewResponse: Decodable {
    let ok: Bool
    let stats: PerformanceStats
    let calendar: CalendarPayload
    let equity: EquityPayload
}

struct PerformanceStats: Decodable {
    let netPnl: Double
    let trades: Int
    let wins: Int
    let losses: Int
    let winRate: Double
    let balance: Double
    let equity: Double
    let averageWin: Double
    let averageLoss: Double
    let profitFactor: Double?
    let maxDrawdownPct: Double?
    let currency: String
}

struct CalendarPayload: Decodable {
    let month: String
    let summary: CalendarSummary
    let days: [TradingDay]
    let currency: String
    let timezone: String
    let dataQuality: DataQuality?
}

struct CalendarResponse: Decodable {
    let ok: Bool
    let month: String
    let summary: CalendarSummary
    let days: [TradingDay]
    let currency: String
    let timezone: String
    let dataQuality: DataQuality?

    var payload: CalendarPayload {
        CalendarPayload(month: month, summary: summary, days: days, currency: currency,
                        timezone: timezone, dataQuality: dataQuality)
    }
}

struct CalendarSummary: Decodable {
    let netPnl: Double
    let trades: Int
    let wins: Int
    let losses: Int
    let winRate: Double
    let tradingDays: Int
    let returnPct: Double?
}

struct TradingDay: Decodable, Identifiable {
    let date: String
    let netPnl: Double
    let trades: Int
    let wins: Int
    let losses: Int
    let unmatched: Int?
    var id: String { date }
}

struct DataQuality: Decodable {
    let complete: Bool
    let unmatchedClosedPositions: Int
}

struct EquityPayload: Decodable {
    let currency: String
    let timezone: String
    let points: [EquityPoint]
    let tradeCount: Int
}

struct EquityPoint: Decodable, Identifiable {
    let at: String?
    let date: String?
    let cumulativePnl: Double
    let tradeIndex: Int?
    var id: String { at ?? "\(tradeIndex ?? 0)-\(cumulativePnl)" }
}

struct TradesResponse: Decodable {
    let ok: Bool
    let currency: String
    let timezone: String
    let trades: [Trade]
    let total: Int
}

struct Trade: Decodable, Identifiable {
    let positionId: Int
    let symbol: String
    let direction: String
    let volume: Double
    let entryPrice: Double?
    let exitPrice: Double?
    let openedAt: String
    let closedAt: String
    let durationSeconds: Int
    let netPnl: Double
    let fees: Double
    let deals: Int
    var id: Int { positionId }
}

struct AnalyticsResponse: Decodable {
    let ok: Bool
    let currency: String
    let symbol: [AnalyticsRow]
    let weekday: [AnalyticsRow]
    let direction: [AnalyticsRow]
    let session: [AnalyticsRow]
    let holdingTime: [AnalyticsRow]
    let month: [AnalyticsRow]
}

struct AnalyticsRow: Decodable, Identifiable {
    let label: String
    let netPnl: Double
    let trades: Int
    let winRate: Double
    var id: String { label }
}

struct CoachResponse: Decodable {
    let ok: Bool
    let reviewType: String
    let score: CoachScore
    let insights: [CoachInsight]
    let summary: CoachSummary
    let dataSufficiency: String
    let telemetry: CoachTelemetry
}

struct CoachScore: Decodable {
    let available: Bool
    let minimumTrades: Int?
    let sampleSize: Int
    let score: Double?
    let components: [String: Double]
}

struct CoachInsight: Decodable, Identifiable {
    let title: String
    let observation: String
    let financialImpactIfMeasurable: Double?
    let recommendation: String
    let confidence: Double
    let pattern: String
    let severity: String
    let sampleSize: Int
    var id: String { "\(pattern)-\(sampleSize)" }
}

struct CoachSummary: Decodable {
    let mainImprovement: String
}

struct CoachTelemetry: Decodable {
    let eventsAnalyzed: Int
    let externalApiCost: Int
}

struct BrokerCatalogResponse: Decodable {
    let ok: Bool
    let brokers: [Broker]
}

struct Broker: Decodable, Identifiable, Hashable {
    let name: String
    let servers: [String]
    let manualServerAllowed: Bool?
    var id: String { name }
}

struct ConnectAccountResponse: Decodable {
    let ok: Bool
    let account: TradingAccount
    let message: String
}

struct SyncResponse: Decodable {
    let ok: Bool
    let queued: Bool
    let cooldownSeconds: Int?
}

struct MessageResponse: Decodable {
    let ok: Bool
    let error: String?
}
