import SwiftUI

@main
struct BectanseTrackApp: App {
    @StateObject private var store = AppStore()

    var body: some Scene {
        WindowGroup {
            AppEntryView()
                .environmentObject(store)
                .preferredColorScheme(.dark)
                .task { await store.bootstrap() }
        }
    }
}
struct AppEntryView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        ZStack {
            Brand.background.ignoresSafeArea()
            switch store.phase {
            case .launching:
                LaunchView()
                    .transition(.opacity)
            case .signedOut:
                AuthenticationView()
                    .transition(.opacity)
            case .ready:
                RootView()
                    .transition(.opacity)
            }
        }
        .animation(.easeInOut(duration: 0.35), value: store.phase)
        .alert("Bectanse Track", isPresented: Binding(
            get: { store.alertMessage != nil },
            set: { if !$0 { store.alertMessage = nil } }
        )) {
            Button("Fermer", role: .cancel) { store.alertMessage = nil }
        } message: {
            Text(store.alertMessage ?? "")
        }
    }
}
