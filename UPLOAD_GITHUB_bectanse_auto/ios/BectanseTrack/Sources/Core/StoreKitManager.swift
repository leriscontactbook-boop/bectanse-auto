import Foundation
import StoreKit

@MainActor
final class StoreKitManager: ObservableObject {
    struct AppProduct: Identifiable {
        let id: String
        let displayName: String
        let description: String
        let price: String
        let hasFreeTrialOffer: Bool
        let isEligibleForFreeTrial: Bool
        let freeTrialDuration: String?
        let renewalPeriod: String
        let product: Product
    }

    @Published private(set) var products: [AppProduct] = []
    @Published private(set) var isLoading = false
    @Published private(set) var purchasingProductID: String?
    @Published private(set) var isRestoring = false
    @Published private(set) var statusMessage: String?

    private var productIDs: [String] = []
    private var accountToken: UUID?
    private var synchronizeWithServer: ((String) async throws -> Void)?
    private var updatesTask: Task<Void, Never>?

    func configure(
        productIDs: [String],
        accountToken: UUID,
        synchronizeWithServer: @escaping (String) async throws -> Void
    ) {
        self.productIDs = productIDs
        self.accountToken = accountToken
        self.synchronizeWithServer = synchronizeWithServer
    }

    func initialize() async {
        await loadProducts()
        if updatesTask == nil {
            updatesTask = Task { await observeUpdates() }
        }
    }

    func reset() {
        updatesTask?.cancel()
        updatesTask = nil
        products = []
        productIDs = []
        accountToken = nil
        synchronizeWithServer = nil
        statusMessage = nil
    }

    func loadProducts() async {
        guard !productIDs.isEmpty else { return }
        isLoading = true
        statusMessage = nil
        defer { isLoading = false }
        do {
            let loaded = try await Product.products(for: productIDs)
            let subscriptions = loaded
                .filter { $0.type == .autoRenewable }
                .sorted { $0.price < $1.price }
            var catalog: [AppProduct] = []
            for product in subscriptions {
                let subscription = product.subscription
                let introductoryOffer = subscription?.introductoryOffer
                let hasFreeTrial = introductoryOffer?.paymentMode == .freeTrial
                let isEligible: Bool
                if hasFreeTrial, let subscription {
                    isEligible = await subscription.isEligibleForIntroOffer
                } else {
                    isEligible = false
                }
                catalog.append(AppProduct(
                    id: product.id,
                    displayName: product.displayName,
                    description: product.description,
                    price: product.displayPrice,
                    hasFreeTrialOffer: hasFreeTrial,
                    isEligibleForFreeTrial: isEligible,
                    freeTrialDuration: hasFreeTrial ? Self.durationText(for: introductoryOffer) : nil,
                    renewalPeriod: Self.periodText(subscription?.subscriptionPeriod),
                    product: product
                ))
            }
            products = catalog
            if products.isEmpty {
                statusMessage = "Les abonnements Apple sont en cours d’activation."
            } else if !products.contains(where: { $0.hasFreeTrialOffer }) {
                statusMessage = "L’offre d’essai de 7 jours n’est pas encore activée dans l’App Store."
            } else if !products.contains(where: { $0.isEligibleForFreeTrial }) {
                statusMessage = "Cet identifiant Apple n’est pas éligible à un nouvel essai gratuit. Vous pouvez vous abonner ou restaurer un achat existant."
            }
        } catch {
            products = []
            statusMessage = "Impossible de charger les abonnements Apple pour le moment."
        }
    }

    private static func durationText(for offer: Product.SubscriptionOffer?) -> String? {
        guard let offer else { return nil }
        let count = offer.period.value * offer.periodCount
        switch offer.period.unit {
        case .day:
            return count == 1 ? "1 jour" : "\(count) jours"
        case .week:
            if count == 1 { return "7 jours" }
            return "\(count) semaines"
        case .month:
            return count == 1 ? "1 mois" : "\(count) mois"
        case .year:
            return count == 1 ? "1 an" : "\(count) ans"
        @unknown default:
            return nil
        }
    }

    private static func periodText(_ period: Product.SubscriptionPeriod?) -> String {
        guard let period else { return "période" }
        switch period.unit {
        case .day:
            return period.value == 1 ? "jour" : "\(period.value) jours"
        case .week:
            return period.value == 1 ? "semaine" : "\(period.value) semaines"
        case .month:
            return period.value == 1 ? "mois" : "\(period.value) mois"
        case .year:
            return period.value == 1 ? "an" : "\(period.value) ans"
        @unknown default:
            return "période"
        }
    }

    func purchase(_ productID: String) async -> Bool {
        guard let match = products.first(where: { $0.id == productID }),
              let accountToken,
              synchronizeWithServer != nil else {
            statusMessage = "L’achat n’est pas encore disponible."
            return false
        }
        purchasingProductID = productID
        statusMessage = nil
        defer { purchasingProductID = nil }
        do {
            switch try await match.product.purchase(options: [.appAccountToken(accountToken)]) {
            case .success(let verification):
                return await synchronize(verification, showSuccess: true)
            case .userCancelled:
                statusMessage = "Paiement annulé."
            case .pending:
                statusMessage = "Paiement en attente de validation Apple."
            @unknown default:
                statusMessage = "Statut de paiement inconnu."
            }
        } catch {
            statusMessage = "Le paiement n’a pas pu être finalisé."
        }
        return false
    }

    func restorePurchases() async {
        guard synchronizeWithServer != nil else {
            statusMessage = "La restauration n’est pas encore disponible."
            return
        }
        isRestoring = true
        statusMessage = nil
        defer { isRestoring = false }
        do {
            try await StoreKit.AppStore.sync()
            var restored = 0
            for await result in Transaction.currentEntitlements {
                if await synchronize(result, showSuccess: false) { restored += 1 }
            }
            statusMessage = restored == 0 ? "Aucun abonnement actif trouvé." : "Abonnement restauré."
        } catch StoreKitError.userCancelled {
            statusMessage = "Restauration annulée."
        } catch {
            statusMessage = "La restauration n’a pas pu être terminée."
        }
    }

    private func observeUpdates() async {
        for await result in Transaction.updates {
            guard !Task.isCancelled else { return }
            _ = await synchronize(result, showSuccess: false)
        }
    }

    private func synchronize(_ verification: VerificationResult<Transaction>, showSuccess: Bool) async -> Bool {
        guard case .verified(let transaction) = verification, let synchronizeWithServer else {
            statusMessage = "Transaction Apple non vérifiée."
            return false
        }
        do {
            try await synchronizeWithServer(verification.jwsRepresentation)
            await transaction.finish()
            if showSuccess { statusMessage = "Abonnement validé. Votre accès est actif." }
            return true
        } catch {
            statusMessage = "Achat reçu. La validation du compte sera relancée automatiquement."
            return false
        }
    }
}
