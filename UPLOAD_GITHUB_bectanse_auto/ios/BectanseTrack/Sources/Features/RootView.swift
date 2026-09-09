import SwiftUI

struct RootView: View {
    @EnvironmentObject private var store: AppStore
    @State private var showsAccountConnection = false
    @State private var showsProfile = false

    var body: some View {
        ZStack {
            Brand.background.ignoresSafeArea()
            VStack(spacing: 0) {
                AppHeader(
                    connect: { showsAccountConnection = true },
                    profile: { showsProfile = true }
                )
                if store.entitlements.allowed {
                    tabContent
                } else {
                    AccessExpiredView()
                }
            }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if store.entitlements.allowed { BottomNavigation() }
        }
        .sheet(isPresented: $showsAccountConnection) {
            ConnectAccountSheet()
                .presentationBackground(Brand.background)
        }
        .sheet(isPresented: $showsProfile) {
            ProfileSheet()
                .presentationBackground(Brand.background)
        }
        .refreshable {
            await store.reloadAccounts()
            await store.loadOverview()
        }
    }

    @ViewBuilder private var tabContent: some View {
        switch store.selectedTab {
        case .overview: OverviewView(connect: { showsAccountConnection = true })
        case .trades: TradesView()
        case .calendar: CalendarView()
        case .analytics: AnalyticsView()
        case .coach: CoachView()
        }
    }
}

private struct AppHeader: View {
    @EnvironmentObject private var store: AppStore
    let connect: () -> Void
    let profile: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            BrandLogo(size: 38, cornerRadius: 10)
            VStack(alignment: .leading, spacing: 1) {
                Text("BECTANSE").font(.system(size: 13, weight: .heavy, design: .rounded)).tracking(2.4)
                Text("TRACK").font(.trackLabel(8)).tracking(3).foregroundStyle(Brand.orange)
            }
            Spacer()
            if store.entitlements.allowed {
                Button(action: connect) {
                    Image(systemName: "plus")
                        .font(.system(size: 15, weight: .bold))
                        .frame(width: 42, height: 42)
                        .foregroundStyle(.black)
                        .background(Brand.orange)
                        .clipShape(RoundedRectangle(cornerRadius: 13))
                }
                .accessibilityLabel("Connecter un compte")
            }
            Button(action: profile) {
                Text(String(store.member?.firstName.prefix(1) ?? "B"))
                    .font(.system(size: 15, weight: .bold, design: .rounded))
                    .frame(width: 42, height: 42)
                    .foregroundStyle(Brand.orange)
                    .background(Brand.orange.opacity(0.10))
                    .overlay(Circle().stroke(Brand.orange.opacity(0.38)))
                    .clipShape(Circle())
            }
            .accessibilityLabel("Profil")
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 11)
        .background(Brand.background.opacity(0.97))
        .overlay(alignment: .bottom) { Rectangle().fill(Brand.line).frame(height: 1) }
    }
}

private struct BottomNavigation: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        HStack(spacing: 2) {
            ForEach(AppTab.allCases) { tab in
                Button {
                    store.selectedTab = tab
                } label: {
                    VStack(spacing: 5) {
                        Image(systemName: tab.icon)
                            .font(.system(size: 18, weight: store.selectedTab == tab ? .semibold : .regular))
                        Text(tab.title)
                            .font(.system(size: 9, weight: .bold, design: .rounded))
                            .lineLimit(1)
                        Capsule()
                            .fill(store.selectedTab == tab ? Brand.orange : .clear)
                            .frame(width: 22, height: 2)
                    }
                    .foregroundStyle(store.selectedTab == tab ? Brand.orange : Brand.secondaryText)
                    .frame(maxWidth: .infinity, minHeight: 56)
                    .background(store.selectedTab == tab ? Brand.orange.opacity(0.075) : .clear)
                    .clipShape(RoundedRectangle(cornerRadius: 15))
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(tab.title)
                .accessibilityAddTraits(store.selectedTab == tab ? .isSelected : [])
            }
        }
        .padding(.horizontal, 10)
        .padding(.top, 7)
        .background(.ultraThinMaterial)
        .background(Brand.background.opacity(0.92))
        .overlay(alignment: .top) { Rectangle().fill(Brand.line).frame(height: 1) }
    }
}

private struct AccessExpiredView: View {
    @EnvironmentObject private var store: AppStore
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                Text("ACCÈS SUSPENDU").font(.trackLabel(10)).tracking(2).foregroundStyle(Brand.orange)
                Text("Vos données restent\nà leur place.")
                    .font(.trackDisplay(44))
                Text("L’accès au journal et les synchronisations sont arrêtés tant qu’aucun abonnement Bectanse Académie ou Bectanse Track n’est actif.")
                    .foregroundStyle(Brand.secondaryText)
                    .lineSpacing(4)
                VStack(alignment: .leading, spacing: 14) {
                    Label("Historique conservé", systemImage: "externaldrive")
                    Label("Connexion MT5 suspendue", systemImage: "pause.circle")
                    Label("Réactivation dès validation", systemImage: "checkmark.shield")
                }
                .font(.subheadline.weight(.semibold))
                .trackCard()
                Button("Vérifier mon accès") {
                    Task { await store.refreshSession() }
                }
                .buttonStyle(PrimaryButtonStyle())
            }
            .padding(22)
        }
    }
}

private struct ProfileSheet: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    VStack(alignment: .leading, spacing: 7) {
                        Text(store.member?.name ?? "Trader").font(.trackDisplay(34))
                        Text(store.member?.email ?? "").foregroundStyle(Brand.secondaryText)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .trackCard()

                    VStack(alignment: .leading, spacing: 15) {
                        ProfileLine(label: "Formule", value: store.entitlements.plan.replacingOccurrences(of: "_", with: " "))
                        ProfileLine(label: "Source", value: store.entitlements.source.replacingOccurrences(of: "_", with: " "))
                        ProfileLine(label: "Comptes", value: "\(store.accounts.count) / \(store.entitlements.maxAccounts)")
                        ProfileLine(label: "Fuseau", value: store.profile.timezone)
                    }
                    .trackCard()

                    Button(role: .destructive) {
                        Task { await store.logout() }
                        dismiss()
                    } label: {
                        Label("Se déconnecter", systemImage: "rectangle.portrait.and.arrow.right")
                            .frame(maxWidth: .infinity, minHeight: 52)
                    }
                    .buttonStyle(.bordered)
                    .tint(Brand.negative)
                }
                .padding(18)
            }
            .background(Brand.background)
            .navigationTitle("Profil")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Fermer") { dismiss() }.foregroundStyle(Brand.orange)
                }
            }
        }
    }
}

private struct ProfileLine: View {
    let label: String
    let value: String
    var body: some View {
        HStack {
            Text(label).foregroundStyle(Brand.secondaryText)
            Spacer()
            Text(value).fontWeight(.semibold).multilineTextAlignment(.trailing)
        }
        .font(.subheadline)
    }
}
