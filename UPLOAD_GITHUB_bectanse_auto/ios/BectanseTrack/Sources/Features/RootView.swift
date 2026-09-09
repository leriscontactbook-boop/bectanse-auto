import SwiftUI

struct RootView: View {
    @EnvironmentObject private var store: AppStore
    @State private var showsAccountConnection = false
    @State private var showsProfile = false

    var body: some View {
        ZStack {
            Brand.background.ignoresSafeArea()
            RadialGradient(
                colors: [Brand.orange.opacity(0.028), .clear],
                center: .topTrailing,
                startRadius: 0,
                endRadius: 320
            )
            .ignoresSafeArea()
            .allowsHitTesting(false)
            VStack(spacing: 0) {
                AppHeader(
                    connect: { showsAccountConnection = true },
                    profile: { showsProfile = true }
                )
                if store.entitlements.allowed {
                    tabContent
                        .id(store.selectedTab)
                        .transition(.opacity)
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
        HStack(spacing: 11) {
            BrandLogo(size: 34, cornerRadius: 8)
            VStack(alignment: .leading, spacing: 1) {
                Text("BECTANSE")
                    .font(TrackType.label(12))
                    .tracking(2.25)
                Text("TRACK")
                    .font(.trackLabel(7.5))
                    .tracking(2.75)
                    .foregroundStyle(Brand.orange)
            }
            Spacer()
            if store.entitlements.allowed {
                Button {
                    Tactile.impact()
                    connect()
                } label: {
                    Image(systemName: "plus")
                        .font(.system(size: 14, weight: .semibold))
                        .frame(width: 40, height: 40)
                        .foregroundStyle(Brand.orange)
                        .background(Brand.surface)
                        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(Brand.orange.opacity(0.35), lineWidth: 0.75))
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                }
                .buttonStyle(TactileCardButtonStyle())
                .accessibilityLabel("Connecter un compte")
            }
            Button {
                Tactile.impact()
                profile()
            } label: {
                Text(String(store.member?.firstName.prefix(1) ?? "B"))
                    .font(TrackType.label(14))
                    .frame(width: 40, height: 40)
                    .foregroundStyle(Brand.orange)
                    .background(Brand.orange.opacity(0.075))
                    .overlay(Circle().stroke(Brand.orange.opacity(0.30), lineWidth: 0.75))
                    .clipShape(Circle())
            }
            .buttonStyle(TactileCardButtonStyle())
            .accessibilityLabel("Profil")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(Brand.backgroundElevated.opacity(0.97))
        .overlay(alignment: .bottom) { Rectangle().fill(Brand.line).frame(height: 1) }
    }
}

private struct BottomNavigation: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        HStack(spacing: 3) {
            ForEach(AppTab.allCases) { tab in
                Button {
                    guard store.selectedTab != tab else { return }
                    Tactile.selection()
                    withAnimation(Brand.Motion.spring) { store.selectedTab = tab }
                } label: {
                    VStack(spacing: 4) {
                        Image(systemName: tab.icon)
                            .symbolRenderingMode(.monochrome)
                            .font(.system(size: 17, weight: store.selectedTab == tab ? .semibold : .regular))
                        Text(tab.title)
                            .font(TrackType.label(9))
                            .lineLimit(1)
                            .minimumScaleFactor(0.72)
                        Capsule()
                            .fill(store.selectedTab == tab ? Brand.orange : .clear)
                            .frame(width: 18, height: 2)
                    }
                    .foregroundStyle(store.selectedTab == tab ? Brand.orange : Brand.secondaryText)
                    .frame(maxWidth: .infinity, minHeight: 50)
                    .background(store.selectedTab == tab ? Brand.orange.opacity(0.065) : .clear)
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(tab.title)
                .accessibilityAddTraits(store.selectedTab == tab ? .isSelected : [])
            }
        }
        .padding(.horizontal, 8)
        .padding(.top, 6)
        .padding(.bottom, 2)
        .background(.thinMaterial)
        .background(Brand.backgroundElevated.opacity(0.94))
        .overlay(alignment: .top) { Rectangle().fill(Brand.line).frame(height: 1) }
    }
}

private struct AccessExpiredView: View {
    @EnvironmentObject private var store: AppStore
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                Text("ACCÈS MEMBRE REQUIS").font(.trackLabel(10)).tracking(2).foregroundStyle(Brand.orange)
                Text("Votre accès Académie\nn’est pas actif.")
                    .font(.trackDisplay(38))
                    .tracking(-1.1)
                Text("Cette première version de Bectanse Track est exclusivement réservée aux membres Bectanse Académie disposant d’un abonnement actif.")
                    .foregroundStyle(Brand.secondaryText)
                    .lineSpacing(4)
                VStack(alignment: .leading, spacing: 14) {
                    Label("Historique conservé", systemImage: "externaldrive")
                    Label("Connexion MT5 suspendue", systemImage: "pause.circle")
                    Label("Réactivation automatique avec l’Académie", systemImage: "checkmark.shield")
                }
                .font(TrackType.body(14, weight: .semibold))
                .trackCard()
                Button {
                    Task { await store.logout() }
                } label: {
                    Label("Utiliser un autre code BCT", systemImage: "rectangle.portrait.and.arrow.right")
                        .frame(maxWidth: .infinity, minHeight: 52)
                }
                .buttonStyle(SecondaryButtonStyle())
                .tint(Brand.orange)
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
                        Text(store.member?.name ?? "Trader").font(.trackDisplay(30)).tracking(-0.7)
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
                .monospacedDigit()
        }
        .font(.subheadline)
    }
}
