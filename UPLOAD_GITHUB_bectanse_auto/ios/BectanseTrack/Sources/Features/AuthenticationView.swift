import SwiftUI

struct AuthenticationView: View {
    @EnvironmentObject private var store: AppStore
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var mode: Mode = .academy
    @State private var code = ""
    @State private var name = ""
    @State private var email = ""
    @State private var appeared = false

    enum Mode: String, CaseIterable, Identifiable {
        case academy = "Membre Académie"
        case trial = "Essai 7 jours"
        var id: String { rawValue }
    }

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

                    accessSelector

                    VStack(spacing: 13) {
                        if mode == .academy {
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
                        } else {
                            TextField("Nom", text: $name).textContentType(.name).trackField()
                            TextField("E-mail", text: $email)
                                .textInputAutocapitalization(.never)
                                .keyboardType(.emailAddress)
                                .textContentType(.emailAddress)
                                .autocorrectionDisabled()
                                .trackField()
                            Button {
                                Tactile.impact()
                                Task { await store.startTrial(name: name, email: email) }
                            } label: {
                                HStack {
                                    Text(store.isLoading ? "Création en cours" : "Continuer vers Apple")
                                    Spacer()
                                    Image(systemName: "arrow.right")
                                }
                            }
                            .buttonStyle(PrimaryButtonStyle())
                            .disabled(name.count < 2 || !email.contains("@") || store.isLoading)
                            Text("Ce formulaire ne donne aucun accès. L’essai de 7 jours démarre uniquement après confirmation de l’abonnement et du moyen de paiement par Apple. Un seul essai est accordé par identifiant Apple et groupe d’abonnements.")
                                .font(TrackType.body(12))
                                .foregroundStyle(Brand.secondaryText)
                                .lineSpacing(3)
                        }
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

    private var accessSelector: some View {
        HStack(spacing: 4) {
            ForEach(Mode.allCases) { item in
                Button {
                    Tactile.selection()
                    withAnimation(Brand.Motion.quick) { mode = item }
                } label: {
                    VStack(spacing: 6) {
                        Text(item.rawValue)
                            .font(TrackType.body(12, weight: .semibold))
                        Capsule().fill(mode == item ? Brand.orange : .clear).frame(width: 20, height: 2)
                    }
                    .frame(maxWidth: .infinity, minHeight: 44)
                    .foregroundStyle(mode == item ? Brand.orange : Brand.secondaryText)
                    .background(mode == item ? Brand.orange.opacity(0.065) : .clear)
                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                }
                .buttonStyle(TactileCardButtonStyle())
            }
        }
        .padding(4)
        .background(Brand.surface)
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(Brand.lineStrong, lineWidth: 0.75))
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
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
