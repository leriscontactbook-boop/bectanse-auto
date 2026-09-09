import Foundation
import StoreKit

@MainActor
final class StoreKitManager: ObservableObject {
    struct AppProduct: Identifiable {
        let id: String
        let displayName: String
        let description: String
        let price: String
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
            products = loaded
                .filter { $0.type == .autoRenewable }
                .sorted { $0.price < $1.price }
                .map {
                    AppProduct(
                        id: $0.id,
                        displayName: $0.displayName,
                        description: $0.description,
                        price: $0.displayPrice,
                        product: $0
                    )
                }
            if products.isEmpty {
                statusMessage = "Les abonnements Apple sont en cours d’activation."
            }
        } catch {
            products = []
            statusMessage = "Impossible de charger les abonnements Apple pour le moment."
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
