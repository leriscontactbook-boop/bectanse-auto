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
                        BrandLogo(size: 52, cornerRadius: 13)
                        VStack(alignment: .leading, spacing: 1) {
                            Text("BECTANSE").font(.system(size: 18, weight: .heavy, design: .rounded)).tracking(3)
                            Text("TRACK").font(.trackLabel(10)).tracking(4).foregroundStyle(Brand.orange)
                        }
                    }
                    .padding(.top, 26)

                    VStack(alignment: .leading, spacing: 10) {
                        Text("Votre trading,\nsans angle mort.")
                            .font(.trackDisplay(48))
                            .fixedSize(horizontal: false, vertical: true)
                        Text("Connectez votre compte MT5. Bectanse Track transforme vos données vérifiées en décisions plus disciplinées.")
                            .font(.body)
                            .foregroundStyle(Brand.secondaryText)
                            .lineSpacing(4)
                    }

                    Picker("Accès", selection: $mode) {
                        ForEach(Mode.allCases) { Text($0.rawValue).tag($0) }
                    }
                    .pickerStyle(.segmented)

                    VStack(spacing: 13) {
                        if mode == .academy {
                            TextField("Code BCT", text: $code)
                                .textInputAutocapitalization(.characters)
                                .autocorrectionDisabled()
                                .textContentType(.password)
                                .trackField()
                            Button {
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
                                Task { await store.startTrial(name: name, email: email) }
                            } label: {
                                HStack {
                                    Text(store.isLoading ? "Création en cours" : "Commencer gratuitement")
                                    Spacer()
                                    Image(systemName: "arrow.right")
                                }
                            }
                            .buttonStyle(PrimaryButtonStyle())
                            .disabled(name.count < 2 || !email.contains("@") || store.isLoading)
                            Text("7 jours complets. Un seul essai par personne. L’accès se ferme automatiquement à la fin de la période si aucun abonnement n’est actif.")
                                .font(.caption)
                                .foregroundStyle(Brand.secondaryText)
                                .lineSpacing(3)
                        }
                    }
                    .trackCard()

                    HStack(spacing: 16) {
                        Label("Données chiffrées", systemImage: "lock.shield")
                        Label("Lecture MT5", systemImage: "eye")
                    }
                    .font(.caption.weight(.semibold))
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
            withAnimation(.easeOut(duration: 0.7)) { appeared = true }
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
                context.stroke(line, with: .color(Brand.orange.opacity(0.13)), lineWidth: 1.4)
            }
        }
        .ignoresSafeArea()
    }
}
