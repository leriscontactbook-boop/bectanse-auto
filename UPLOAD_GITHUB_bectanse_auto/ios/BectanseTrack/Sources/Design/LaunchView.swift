import SwiftUI

struct LaunchView: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var revealed = false

    var body: some View {
        ZStack {
            TradingBackdrop(active: revealed && !reduceMotion)
            LinearGradient(
                colors: [.clear, Brand.background.opacity(0.15), Brand.background],
                startPoint: .top,
                endPoint: .bottom
            )
            VStack(spacing: 20) {
                BrandLogo(size: 108, cornerRadius: 24)
                    .shadow(color: Brand.orange.opacity(revealed ? 0.42 : 0), radius: 34)
                    .scaleEffect(revealed ? 1 : 0.84)
                    .opacity(revealed ? 1 : 0)
                VStack(spacing: 5) {
                    Text("BECTANSE")
                        .font(.system(size: 22, weight: .heavy, design: .rounded))
                        .tracking(5)
                    Text("TRACK")
                        .font(.trackLabel(12))
                        .tracking(6)
                        .foregroundStyle(Brand.orange)
                }
                .offset(y: revealed ? 0 : 12)
                .opacity(revealed ? 1 : 0)
                Text("Vos performances. Automatiquement.")
                    .font(.subheadline)
                    .foregroundStyle(Brand.secondaryText)
                    .opacity(revealed ? 1 : 0)
            }
        }
        .ignoresSafeArea()
        .onAppear {
            withAnimation(.spring(response: 0.85, dampingFraction: 0.78).delay(0.12)) {
                revealed = true
            }
        }
    }
}

private struct TradingBackdrop: View {
    let active: Bool
    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 24, paused: !active)) { timeline in
            Canvas { context, size in
                let progress = timeline.date.timeIntervalSinceReferenceDate
                var grid = Path()
                for x in stride(from: 0.0, through: size.width, by: 46) {
                    grid.move(to: CGPoint(x: x, y: 0)); grid.addLine(to: CGPoint(x: x, y: size.height))
                }
                for y in stride(from: 0.0, through: size.height, by: 46) {
                    grid.move(to: CGPoint(x: 0, y: y)); grid.addLine(to: CGPoint(x: size.width, y: y))
                }
                context.stroke(grid, with: .color(.white.opacity(0.025)), lineWidth: 0.6)

                let width: CGFloat = 14
                for index in 0..<24 {
                    let x = CGFloat(index) * (size.width / 20) - 22
                    let wave = sin(Double(index) * 0.75 + progress * 0.45)
                    let center = size.height * (0.42 + CGFloat(wave) * 0.12)
                    let height = 18 + CGFloat(abs(cos(Double(index) * 1.21))) * 54
                    let rising = index % 3 != 0
                    let color = rising ? Brand.positive.opacity(0.15) : Brand.negative.opacity(0.12)
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
