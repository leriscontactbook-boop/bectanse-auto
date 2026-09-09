#if DEBUG
import Foundation

@MainActor
extension AppStore {
    func loadUITestData() {
        member = MemberProfile(name: "Leris Luketo", firstName: "Leris", email: "membre@bectanse.com", accessLevel: "member")
        profile = JournalProfile(timezone: "Europe/Paris")
        entitlements = Entitlements(
            allowed: true, source: "ACADEMY_INCLUDED", plan: "ACADEMY_INCLUDED",
            maxAccounts: 10, advancedAnalytics: true, multiAccount: true,
            features: ["coach.daily": true, "coach.weekly": true, "coach.monthly": true]
        )
        accounts = [TradingAccount(
            id: 1, provider: "MT5", platform: "MT5", displayName: "Compte principal",
            loginMasked: "••••5495", broker: "PU Prime", server: "PUPrime-Live 6",
            currency: "EUR", accountType: "Raw", balance: 51716.17, equity: 51835.69,
            accessMode: "READ_ONLY", status: "SYNCED", syncStatus: "IDLE",
            lastSyncAt: "2026-09-09T08:25:00+02:00", lastSuccessfulSyncAt: "2026-09-09T08:25:00+02:00",
            lastErrorCode: nil, lastErrorMessage: nil
        )]
        selectedAccountID = 1
        let days = [
            TradingDay(date: "2026-09-01", netPnl: 482, trades: 7, wins: 5, losses: 2, unmatched: 0),
            TradingDay(date: "2026-09-02", netPnl: -127, trades: 4, wins: 2, losses: 2, unmatched: 0),
            TradingDay(date: "2026-09-03", netPnl: 319, trades: 6, wins: 4, losses: 2, unmatched: 0),
            TradingDay(date: "2026-09-04", netPnl: 84, trades: 2, wins: 2, losses: 0, unmatched: 0),
            TradingDay(date: "2026-09-07", netPnl: 421, trades: 5, wins: 3, losses: 2, unmatched: 0),
            TradingDay(date: "2026-09-08", netPnl: -530, trades: 8, wins: 4, losses: 4, unmatched: 0),
            TradingDay(date: "2026-09-09", netPnl: 215, trades: 3, wins: 2, losses: 1, unmatched: 0),
        ]
        let summary = CalendarSummary(netPnl: 864, trades: 35, wins: 22, losses: 13, winRate: 62.9, tradingDays: 7, returnPct: 1.7)
        let calendarPayload = CalendarPayload(
            month: "2026-09", summary: summary, days: days, currency: "EUR",
            timezone: "Europe/Paris", dataQuality: DataQuality(complete: true, unmatchedClosedPositions: 0)
        )
        let stats = PerformanceStats(
            netPnl: 4826.40, trades: 157, wins: 88, losses: 69, winRate: 56.1,
            balance: 51716.17, equity: 51835.69, averageWin: 121.30, averageLoss: -84.90,
            profitFactor: 1.82, maxDrawdownPct: -3.2, currency: "EUR"
        )
        let values = [0.0, 120, 80, 330, 280, 610, 540, 864]
        let points = values.enumerated().map {
            EquityPoint(
                at: "2026-09-\(String(format: "%02d", $0.offset + 1))T12:00:00+02:00",
                date: "2026-09-\(String(format: "%02d", $0.offset + 1))",
                cumulativePnl: $0.element,
                tradeIndex: $0.offset
            )
        }
        overview = OverviewResponse(
            ok: true, stats: stats, calendar: calendarPayload,
            equity: EquityPayload(currency: "EUR", timezone: "Europe/Paris", points: points, tradeCount: 157)
        )
        calendar = calendarPayload
        trades = [
            Trade(positionId: 1, symbol: "XAUUSD", direction: "BUY", volume: 0.10, entryPrice: 4394.15, exitPrice: 4402.80, openedAt: "2026-09-09T08:11:00+02:00", closedAt: "2026-09-09T09:02:00+02:00", durationSeconds: 3060, netPnl: 86.50, fees: -2.10, deals: 2),
            Trade(positionId: 2, symbol: "EURUSD", direction: "SELL", volume: 0.35, entryPrice: 1.1824, exitPrice: 1.1840, openedAt: "2026-09-08T14:20:00+02:00", closedAt: "2026-09-08T15:05:00+02:00", durationSeconds: 2700, netPnl: -56.00, fees: -1.20, deals: 2),
        ]
        tradeCurrency = "EUR"
        let analyticsRows = [
            AnalyticsRow(label: "XAUUSD", netPnl: 2814, trades: 74, winRate: 64),
            AnalyticsRow(label: "EURUSD", netPnl: 915, trades: 42, winRate: 57),
        ]
        analytics = AnalyticsResponse(
            ok: true, currency: "EUR", symbol: analyticsRows, weekday: analyticsRows,
            direction: analyticsRows, session: analyticsRows, holdingTime: analyticsRows, month: analyticsRows
        )
        coach = CoachResponse(
            ok: true, reviewType: "daily",
            score: CoachScore(
                available: true, minimumTrades: 20, sampleSize: 157, score: 82,
                components: ["discipline": 78, "risk": 84, "consistency": 75, "execution": 88, "timing": 85]
            ),
            insights: [CoachInsight(
                title: "Votre discipline progresse",
                observation: "Votre taille de position reste plus stable.",
                financialImpactIfMeasurable: 421,
                recommendation: "Conservez la même limite de risque lors de la prochaine session.",
                confidence: 0.91, pattern: "POSITION_SIZE_CONSISTENCY", severity: "MEDIUM", sampleSize: 42
            )],
            summary: CoachSummary(mainImprovement: "Attendez 30 minutes après deux pertes consécutives avant toute nouvelle décision."),
            dataSufficiency: "SUFFICIENT",
            telemetry: CoachTelemetry(eventsAnalyzed: 326, externalApiCost: 0)
        )
    }
}
#endif
