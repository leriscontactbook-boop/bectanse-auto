import SwiftUI

struct LaunchView: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var revealed = false
    @State private var energyPass = false

    var body: some View {
        ZStack {
            TradingBackdrop(active: revealed && !reduceMotion)
            LinearGradient(
                colors: [.clear, Brand.background.opacity(0.24), Brand.background],
                startPoint: .top,
                endPoint: .bottom
            )
            VStack(spacing: 22) {
                BrandLogo(size: 96, cornerRadius: 21)
                    .overlay {
                        LinearGradient(
                            colors: [.clear, Brand.orange.opacity(0.75), .white.opacity(0.50), .clear],
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                        .frame(width: 38)
                        .offset(x: energyPass ? 92 : -92)
                        .blendMode(.screen)
                        .mask(BrandLogo(size: 96, cornerRadius: 21))
                    }
                    .shadow(color: Brand.orange.opacity(revealed ? 0.25 : 0), radius: 28)
                    .scaleEffect(revealed ? 1 : 0.92)
                    .opacity(revealed ? 1 : 0)
                VStack(spacing: 5) {
                    Text("BECTANSE")
                        .font(TrackType.label(20))
                        .tracking(4.8)
                    Text("TRACK")
                        .font(.trackLabel(10))
                        .tracking(5.2)
                        .foregroundStyle(Brand.orange)
                }
                .offset(y: revealed ? 0 : 8)
                .opacity(revealed ? 1 : 0)
                Text("Vos performances. Automatiquement.")
                    .font(TrackType.body(13))
                    .foregroundStyle(Brand.secondaryText)
                    .opacity(revealed ? 1 : 0)
            }
        }
        .ignoresSafeArea()
        .onAppear {
            withAnimation(.easeOut(duration: reduceMotion ? 0 : 0.42)) {
                revealed = true
            }
            guard !reduceMotion else { return }
            withAnimation(.easeInOut(duration: 0.72).delay(0.18)) {
                energyPass = true
            }
        }
    }
}

private struct TradingBackdrop: View {
    let active: Bool
    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 20, paused: !active)) { timeline in
            Canvas { context, size in
                let progress = timeline.date.timeIntervalSinceReferenceDate
                var grid = Path()
                for x in stride(from: 0.0, through: size.width, by: 52) {
                    grid.move(to: CGPoint(x: x, y: 0)); grid.addLine(to: CGPoint(x: x, y: size.height))
                }
                for y in stride(from: 0.0, through: size.height, by: 52) {
                    grid.move(to: CGPoint(x: 0, y: y)); grid.addLine(to: CGPoint(x: size.width, y: y))
                }
                context.stroke(grid, with: .color(.white.opacity(0.020)), lineWidth: 0.5)

                let width: CGFloat = 14
                for index in 0..<24 {
                    let x = CGFloat(index) * (size.width / 20) - 22
                    let wave = sin(Double(index) * 0.75 + progress * 0.45)
                    let center = size.height * (0.42 + CGFloat(wave) * 0.12)
                    let height = 18 + CGFloat(abs(cos(Double(index) * 1.21))) * 54
                    let rising = index % 3 != 0
                    let color = rising ? Brand.positive.opacity(0.095) : Brand.negative.opacity(0.075)
                    var wick = Path()
                    wick.move(to: CGPoint(x: x + width / 2, y: center - height))
                    wick.addLine(to: CGPoint(x: x + width / 2, y: center + height))
                    context.stroke(wick, with: .color(color), lineWidth: 1)
                    context.fill(
                        Path(roundedRect: CGRect(x: x, y: center - height * 0.42, width: width, height: height * 0.84), cornerRadius: 2),
                        with: .color(color)
                    )
                }
            }
        }
        .background(Brand.background)
    }
}
