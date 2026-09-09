import SwiftUI
import UIKit

enum Brand {
    static let background = Color(red: 0.016, green: 0.017, blue: 0.018)
    static let backgroundElevated = Color(red: 0.025, green: 0.026, blue: 0.028)
    static let surface = Color(red: 0.042, green: 0.044, blue: 0.047)
    static let surfaceRaised = Color(red: 0.061, green: 0.064, blue: 0.068)
    static let raised = surfaceRaised
    static let orange = Color(red: 1.0, green: 0.36, blue: 0.055)
    static let orangeSoft = Color(red: 0.92, green: 0.27, blue: 0.035)
    static let positive = Color(red: 0.27, green: 0.78, blue: 0.53)
    static let negative = Color(red: 0.94, green: 0.30, blue: 0.36)
    static let primaryText = Color(red: 0.965, green: 0.958, blue: 0.945)
    static let secondaryText = Color(red: 0.59, green: 0.60, blue: 0.62)
    static let mutedText = Color(red: 0.36, green: 0.37, blue: 0.39)
    static let line = Color.white.opacity(0.085)
    static let lineStrong = Color.white.opacity(0.14)

    enum Space {
        static let xxs: CGFloat = 4
        static let xs: CGFloat = 8
        static let sm: CGFloat = 12
        static let md: CGFloat = 16
        static let lg: CGFloat = 24
        static let xl: CGFloat = 32
    }

    enum Radius {
        static let control: CGFloat = 12
        static let card: CGFloat = 18
        static let large: CGFloat = 22
    }

    enum Motion {
        static let quick = Animation.easeOut(duration: 0.18)
        static let standard = Animation.easeOut(duration: 0.30)
        static let spring = Animation.spring(response: 0.36, dampingFraction: 0.86)
    }
}

enum TrackType {
    static func display(_ size: CGFloat) -> Font {
        .system(size: size, weight: .semibold, design: .default)
    }

    static func heading(_ size: CGFloat) -> Font {
        .system(size: size, weight: .semibold, design: .default)
    }

    static func body(_ size: CGFloat = 15, weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight, design: .default)
    }

    static func label(_ size: CGFloat = 10) -> Font {
        .system(size: size, weight: .semibold, design: .default)
    }

    static func metric(_ size: CGFloat) -> Font {
        .system(size: size, weight: .semibold, design: .default).monospacedDigit()
    }
}

extension Font {
    static func trackDisplay(_ size: CGFloat) -> Font { TrackType.display(size) }
    static func trackLabel(_ size: CGFloat = 10) -> Font { TrackType.label(size) }
    static func trackMetric(_ size: CGFloat) -> Font { TrackType.metric(size) }
}

struct BrandLogo: View {
    let size: CGFloat
    let cornerRadius: CGFloat

    var body: some View {
        Image("BectanseLogo")
            .resizable()
            .interpolation(.high)
            .scaledToFit()
            .frame(width: size, height: size)
            .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
    }
}

struct TrackCard: ViewModifier {
    var padding: CGFloat = Brand.Space.md
    var highlighted = false

    func body(content: Content) -> some View {
        content
            .padding(padding)
            .background {
                ZStack {
                    Brand.surface
                    LinearGradient(
                        colors: [Color.white.opacity(0.026), .clear, Brand.orange.opacity(highlighted ? 0.035 : 0)],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                }
            }
            .overlay {
                RoundedRectangle(cornerRadius: Brand.Radius.card, style: .continuous)
                    .stroke(
                        LinearGradient(
                            colors: [Color.white.opacity(0.13), Brand.line, Color.white.opacity(0.035)],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        ),
                        lineWidth: 0.75
                    )
            }
            .clipShape(RoundedRectangle(cornerRadius: Brand.Radius.card, style: .continuous))
            .shadow(color: .black.opacity(0.24), radius: 16, y: 8)
    }
}

extension View {
    func trackCard(padding: CGFloat = Brand.Space.md, highlighted: Bool = false) -> some View {
        modifier(TrackCard(padding: padding, highlighted: highlighted))
    }

    func financialDigits() -> some View { monospacedDigit() }
}

struct SectionHeading: View {
    let eyebrow: String
    let title: String
    var trailing: String?

    var body: some View {
        HStack(alignment: .bottom, spacing: Brand.Space.sm) {
            VStack(alignment: .leading, spacing: 6) {
                Text(eyebrow.uppercased())
                    .font(.trackLabel(9))
                    .tracking(1.55)
                    .foregroundStyle(Brand.secondaryText)
                Text(title)
                    .font(.trackDisplay(28))
                    .tracking(-0.65)
                    .foregroundStyle(Brand.primaryText)
            }
            Spacer(minLength: Brand.Space.sm)
            if let trailing {
                Text(trailing.uppercased())
                    .font(.trackLabel(8))
                    .tracking(1.05)
                    .foregroundStyle(Brand.orange)
                    .multilineTextAlignment(.trailing)
            }
        }
    }
}

struct MetricTile: View {
    let label: String
    let value: String
    var tone: Color = Brand.primaryText
    var detail: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(label.uppercased())
                .font(.trackLabel(8.5))
                .tracking(1.15)
                .foregroundStyle(Brand.secondaryText)
            Text(value)
                .font(.trackMetric(25))
                .tracking(-0.65)
                .minimumScaleFactor(0.58)
                .lineLimit(1)
                .foregroundStyle(tone)
                .contentTransition(.numericText())
            if let detail {
                Text(detail)
                    .font(.trackLabel(9))
                    .foregroundStyle(Brand.mutedText)
                    .lineLimit(1)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 90, alignment: .leading)
        .trackCard(padding: 14)
    }
}

struct EmptyPanel: View {
    let title: String
    let message: String

    var body: some View {
        VStack(alignment: .leading, spacing: Brand.Space.sm) {
            ZStack {
                Circle().fill(Brand.orange.opacity(0.10)).frame(width: 42, height: 42)
                Image(systemName: "waveform.path.ecg")
                    .font(.system(size: 18, weight: .medium))
                    .foregroundStyle(Brand.orange)
            }
            Text(title)
                .font(TrackType.heading(18))
                .foregroundStyle(Brand.primaryText)
            Text(message)
                .font(TrackType.body(14))
                .lineSpacing(3)
                .foregroundStyle(Brand.secondaryText)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .trackCard()
    }
}

struct LoadingPanel: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var glow = false

    var body: some View {
        VStack(alignment: .leading, spacing: 13) {
            Capsule().fill(Brand.line).frame(width: 92, height: 8)
            Capsule().fill(Brand.lineStrong).frame(height: 22)
            Capsule().fill(Brand.line).frame(width: 180, height: 10)
        }
        .opacity(glow ? 0.9 : 0.48)
        .trackCard()
        .onAppear {
            guard !reduceMotion else { return }
            withAnimation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true)) { glow = true }
        }
    }
}

enum TrackFormat {
    static func money(_ value: Double, currency: String, signed: Bool = false) -> String {
        let formatter = NumberFormatter()
        formatter.locale = Locale(identifier: "fr_FR")
        formatter.numberStyle = .currency
        formatter.currencyCode = currency.isEmpty ? "EUR" : currency
        formatter.maximumFractionDigits = 2
        let raw = formatter.string(from: NSNumber(value: value)) ?? "\(value) \(currency)"
        return signed && value > 0 ? "+\(raw)" : raw
    }

    static func percent(_ value: Double?, signed: Bool = false) -> String {
        guard let value else { return "—" }
        let prefix = signed && value > 0 ? "+" : ""
        return prefix + value.formatted(.number.precision(.fractionLength(1))) + " %"
    }

    static func dateTime(_ raw: String) -> String {
        guard let date = ISO8601DateFormatter().date(from: raw) else { return raw }
        return date.formatted(.dateTime.day().month(.abbreviated).hour().minute().locale(Locale(identifier: "fr_FR")))
    }

    static func duration(_ seconds: Int) -> String {
        if seconds < 3600 { return "\(max(1, seconds / 60)) min" }
        return "\(seconds / 3600) h \((seconds % 3600) / 60) min"
    }
}

struct PrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(TrackType.body(15, weight: .semibold))
            .padding(.horizontal, 18)
            .frame(maxWidth: .infinity, minHeight: 52)
            .foregroundStyle(Color.black.opacity(isEnabled ? 0.92 : 0.48))
            .background {
                LinearGradient(colors: [Brand.orange, Brand.orangeSoft], startPoint: .top, endPoint: .bottom)
                    .opacity(isEnabled ? (configuration.isPressed ? 0.82 : 1) : 0.42)
            }
            .clipShape(RoundedRectangle(cornerRadius: Brand.Radius.control, style: .continuous))
            .overlay(alignment: .top) {
                Rectangle().fill(Color.white.opacity(0.18)).frame(height: 0.5).padding(.horizontal, 10)
            }
            .shadow(color: Brand.orange.opacity(configuration.isPressed ? 0.06 : 0.16), radius: 14, y: 6)
            .scaleEffect(configuration.isPressed ? 0.975 : 1)
            .animation(Brand.Motion.quick, value: configuration.isPressed)
    }
}

struct SecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(TrackType.body(14, weight: .semibold))
            .frame(minHeight: 48)
            .foregroundStyle(Brand.primaryText)
            .background(configuration.isPressed ? Brand.surfaceRaised : Brand.surface)
            .overlay(RoundedRectangle(cornerRadius: Brand.Radius.control, style: .continuous).stroke(Brand.lineStrong, lineWidth: 0.75))
            .clipShape(RoundedRectangle(cornerRadius: Brand.Radius.control, style: .continuous))
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
            .animation(Brand.Motion.quick, value: configuration.isPressed)
    }
}

struct TactileCardButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.975 : 1)
            .opacity(configuration.isPressed ? 0.88 : 1)
            .animation(Brand.Motion.quick, value: configuration.isPressed)
    }
}

struct FieldBackground: ViewModifier {
    @Environment(\.isEnabled) private var isEnabled

    func body(content: Content) -> some View {
        content
            .font(TrackType.body(15))
            .padding(.horizontal, 14)
            .frame(minHeight: 52)
            .background(Brand.surface.opacity(isEnabled ? 1 : 0.5))
            .overlay {
                RoundedRectangle(cornerRadius: Brand.Radius.control, style: .continuous)
                    .stroke(Brand.lineStrong, lineWidth: 0.75)
            }
            .clipShape(RoundedRectangle(cornerRadius: Brand.Radius.control, style: .continuous))
    }
}

extension View {
    func trackField() -> some View { modifier(FieldBackground()) }
}

enum Tactile {
    static func selection() { UISelectionFeedbackGenerator().selectionChanged() }
    static func impact() { UIImpactFeedbackGenerator(style: .soft).impactOccurred() }
    static func success() { UINotificationFeedbackGenerator().notificationOccurred(.success) }
}
