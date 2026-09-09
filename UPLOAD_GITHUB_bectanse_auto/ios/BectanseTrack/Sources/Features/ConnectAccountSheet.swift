import SwiftUI

struct ConnectAccountSheet: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss
    @State private var selectedBroker = ""
    @State private var customBroker = ""
    @State private var selectedServer = ""
    @State private var customServer = ""
    @State private var login = ""
    @State private var password = ""
    @State private var displayName = ""

    private var broker: Broker? { store.brokers.first(where: { $0.name == selectedBroker }) }
    private var usesCustomBroker: Bool { selectedBroker == "Autre broker" }
    private var usesCustomServer: Bool { broker?.servers.isEmpty != false || selectedServer == "Saisie manuelle" }
    private var resolvedBroker: String { usesCustomBroker ? customBroker.trimmingCharacters(in: .whitespacesAndNewlines) : selectedBroker }
    private var resolvedServer: String { usesCustomServer ? customServer.trimmingCharacters(in: .whitespacesAndNewlines) : selectedServer }
    private var canSubmit: Bool {
        !resolvedBroker.isEmpty && !resolvedServer.isEmpty && login.count >= 3 && password.count >= 4
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    VStack(alignment: .leading, spacing: 7) {
                        Text("META TRADER 5").font(.trackLabel(9)).tracking(2).foregroundStyle(Brand.orange)
                        Text("Connecter un compte").font(.trackDisplay(36))
                        Text("Utilisez le serveur exact indiqué dans l’e-mail ou le portail de votre broker.")
                            .font(.subheadline).foregroundStyle(Brand.secondaryText).lineSpacing(3)
                    }

                    fieldSection("Broker") {
                        Menu {
                            ForEach(store.brokers) { item in
                                Button(item.name) {
                                    selectedBroker = item.name
                                    selectedServer = item.servers.first ?? "Saisie manuelle"
                                    customServer = ""
                                }
                            }
                            Button("Autre broker") {
                                selectedBroker = "Autre broker"
                                selectedServer = "Saisie manuelle"
                            }
                        } label: {
                            menuLabel(selectedBroker.isEmpty ? "Sélectionner le broker" : selectedBroker)
                        }
                        if usesCustomBroker {
                            TextField("Nom du broker", text: $customBroker).trackField()
                        }
                    }

                    fieldSection("Serveur MT5") {
                        if let servers = broker?.servers, !servers.isEmpty {
                            Menu {
                                ForEach(servers, id: \.self) { server in Button(server) { selectedServer = server } }
                                Button("Saisie manuelle") { selectedServer = "Saisie manuelle" }
                            } label: {
                                menuLabel(selectedServer.isEmpty ? "Sélectionner le serveur" : selectedServer)
                            }
                        }
                        if usesCustomServer {
                            TextField("Exemple : Broker-MT5-Live01", text: $customServer)
                                .textInputAutocapitalization(.never)
                                .autocorrectionDisabled()
                                .trackField()
                        }
                    }

                    fieldSection("Identifiants") {
                        TextField("Numéro de compte MT5", text: $login)
                            .keyboardType(.numberPad)
                            .textContentType(.username)
                            .trackField()
                        SecureField("Mot de passe investisseur ou principal", text: $password)
                            .textContentType(.password)
                            .trackField()
                        TextField("Nom du compte, facultatif", text: $displayName).trackField()
                    }

                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: "lock.shield.fill").foregroundStyle(Brand.positive)
                        Text("Les identifiants sont chiffrés sur le serveur. Le mot de passe investisseur reste recommandé, mais le mot de passe principal est accepté.")
                            .font(.caption).foregroundStyle(Brand.secondaryText).lineSpacing(2)
                    }
                    .trackCard(padding: 14)

                    Button {
                        let body = ConnectAccountBody(
                            displayName: displayName,
                            broker: resolvedBroker,
                            server: resolvedServer,
                            login: login,
                            password: password
                        )
                        Task {
                            if await store.connectAccount(body) { dismiss() }
                        }
                    } label: {
                        HStack {
                            Text(store.isLoading ? "Connexion sécurisée" : "Connecter le compte")
                            Spacer()
                            Image(systemName: "arrow.right")
                        }
                    }
                    .buttonStyle(PrimaryButtonStyle())
                    .disabled(!canSubmit || store.isLoading)
                }
                .padding(18)
            }
            .scrollDismissesKeyboard(.interactively)
            .background(Brand.background)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Fermer") { dismiss() }.foregroundStyle(Brand.orange)
                }
            }
        }
        .task {
            if store.brokers.isEmpty { await store.loadBrokers() }
        }
    }

    private func fieldSection<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title.uppercased()).font(.trackLabel(9)).tracking(1.4).foregroundStyle(Brand.secondaryText)
            content()
        }
    }

    private func menuLabel(_ label: String) -> some View {
        HStack {
            Text(label).lineLimit(1)
            Spacer()
            Image(systemName: "chevron.up.chevron.down").font(.caption)
        }
        .foregroundStyle(Brand.primaryText)
        .trackField()
    }
}
