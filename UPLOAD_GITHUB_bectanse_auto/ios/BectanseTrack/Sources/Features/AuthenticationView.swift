import SwiftUI

struct AuthenticationView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var code = ""
    @State private var appeared = false

    var body: some View {
        ZStack {
            TradingBackdropForAuth(active: appeared && !reduceMotion)
            LinearGradient(colors: [Brand.background.opacity(0.35), Brand.background], startPoint: .top, endPoint: .bottom)
            ScrollView {
                VStack(alignment: .leading, spacing: 26) {
                    HStack(spacing: 13) {
                        BrandLogo(size: 46, cornerRadius: 11)
                        VStack(alignment: .leading, spacing: 1) {
                            Text("BECTANSE").font(TrackType.label(16)).tracking(2.8)
                            Text("TRACK").font(.trackLabel(8.5)).tracking(3.6).foregroundStyle(Brand.orange)
                        }
                    }
                    .padding(.top, 26)

                    VStack(alignment: .leading, spacing: 10) {
                        Text("Votre trading,\nsans angle mort.")
                            .font(.trackDisplay(40))
                            .tracking(-1.2)
                            .fixedSize(horizontal: false, vertical: true)
                        Text("Connectez votre compte MT5. Bectanse Track transforme vos données vérifiées en décisions plus disciplinées.")
                            .font(TrackType.body(15))
                            .foregroundStyle(Brand.secondaryText)
                            .lineSpacing(4)
                    }

                    VStack(alignment: .leading, spacing: 14) {
                        Text("ACCÈS MEMBRE ACADÉMIE")
                            .font(.trackLabel(10))
                            .tracking(1.8)
                            .foregroundStyle(Brand.orange)
                        TextField("Code BCT", text: $code)
                            .textInputAutocapitalization(.characters)
                            .autocorrectionDisabled()
                            .textContentType(.password)
                            .trackField()
                        Button {
                            Tactile.impact()
                            Task { await store.login(code: code) }
                        } label: {
                            HStack {
                                Text(store.isLoading ? "Connexion en cours" : "Ouvrir mon journal")
                                Spacer()
                                Image(systemName: "arrow.right")
                            }
                        }
                        .buttonStyle(PrimaryButtonStyle())
                        .disabled(code.trimmingCharacters(in: .whitespaces).count < 5 || store.isLoading)
                        Text("Cette première version est exclusivement réservée aux membres dont l’abonnement Bectanse Académie est actif.")
                            .font(TrackType.body(12))
                            .foregroundStyle(Brand.secondaryText)
                            .lineSpacing(3)
                    }
                    .trackCard()

                    HStack(spacing: 16) {
                        Label("Données chiffrées", systemImage: "lock.shield")
                        Label("Lecture MT5", systemImage: "eye")
                    }
                    .font(TrackType.label(11))
                    .foregroundStyle(Brand.secondaryText)
                    .frame(maxWidth: .infinity)
                }
                .padding(.horizontal, 20)
                .padding(.bottom, 34)
                .opacity(appeared ? 1 : 0)
                .offset(y: appeared ? 0 : 16)
            }
            .scrollIndicators(.hidden)
        }
        .onAppear {
            withAnimation(reduceMotion ? nil : .easeOut(duration: 0.5)) { appeared = true }
        }
    }

}

private struct TradingBackdropForAuth: View {
    let active: Bool
    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 20, paused: !active)) { timeline in
            Canvas { context, size in
                let t = timeline.date.timeIntervalSinceReferenceDate
                var line = Path()
                for index in 0...30 {
                    let x = CGFloat(index) / 30 * size.width
                    let y = size.height * 0.22 + CGFloat(sin(Double(index) * 0.56 + t * 0.25)) * 42
                    index == 0 ? line.move(to: CGPoint(x: x, y: y)) : line.addLine(to: CGPoint(x: x, y: y))
                }
                context.stroke(line, with: .color(Brand.orange.opacity(0.09)), lineWidth: 1.1)
            }
        }
        .ignoresSafeArea()
    }
}
