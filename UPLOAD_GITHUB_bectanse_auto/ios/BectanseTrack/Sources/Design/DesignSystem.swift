import SwiftUI
import UIKit

enum Brand {
    static let background = Color(red: 0.018, green: 0.018, blue: 0.018)
    static let surface = Color(red: 0.050, green: 0.050, blue: 0.050)
    static let raised = Color(red: 0.075, green: 0.075, blue: 0.075)
    static let orange = Color(red: 1.0, green: 0.39, blue: 0.0)
    static let positive = Color(red: 0.30, green: 0.85, blue: 0.54)
    static let negative = Color(red: 1.0, green: 0.36, blue: 0.42)
    static let primaryText = Color.white
    static let secondaryText = Color.white.opacity(0.48)
    static let line = Color.white.opacity(0.09)
}

struct BrandLogo: View {
    let size: CGFloat
    let cornerRadius: CGFloat

    var body: some View {
        Group {
            if let url = Bundle.main.url(forResource: "bectanse-app-icon-master", withExtension: "png"),
               let image = UIImage(contentsOfFile: url.path) {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFit()
            } else {
                RoundedRectangle(cornerRadius: cornerRadius)
                    .fill(Brand.surface)
                    .overlay(Text("B").font(.trackDisplay(size * 0.56)).foregroundStyle(Brand.orange))
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: cornerRadius))
    }
}

extension Font {
    static func trackDisplay(_ size: CGFloat) -> Font {
        .system(size: size, weight: .black, design: .rounded)
    }

    static func trackLabel(_ size: CGFloat = 11) -> Font {
        .system(size: size, weight: .bold, design: .rounded)
    }
}

struct TrackCard: ViewModifier {
    var padding: CGFloat = 18
    func body(content: Content) -> some View {
        content
            .padding(padding)
            .background(Brand.surface)
            .overlay(RoundedRectangle(cornerRadius: 22).stroke(Brand.line, lineWidth: 1))
            .clipShape(RoundedRectangle(cornerRadius: 22))
    }
}

extension View {
    func trackCard(padding: CGFloat = 18) -> some View {
        modifier(TrackCard(padding: padding))
    }
}

struct SectionHeading: View {
    let eyebrow: String
    let title: String
    var trailing: String?

    var body: some View {
        HStack(alignment: .bottom) {
            VStack(alignment: .leading, spacing: 5) {
                Text(eyebrow.uppercased())
                    .font(.trackLabel(10))
                    .tracking(2.1)
                    .foregroundStyle(Brand.secondaryText)
                Text(title)
                    .font(.trackDisplay(30))
                    .foregroundStyle(Brand.primaryText)
            }
            Spacer(minLength: 12)
            if let trailing {
                Text(trailing.uppercased())
                    .font(.trackLabel(9))
                    .tracking(1.4)
                    .foregroundStyle(Brand.orange)
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
        VStack(alignment: .leading, spacing: 8) {
            Text(label.uppercased())
                .font(.trackLabel(9))
                .tracking(1.5)
                .foregroundStyle(Brand.secondaryText)
            Text(value)
                .font(.trackDisplay(28))
                .minimumScaleFactor(0.65)
                .lineLimit(1)
                .foregroundStyle(tone)
                .contentTransition(.numericText())
            if let detail {
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(Brand.secondaryText)
                    .lineLimit(1)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 92, alignment: .leading)
        .trackCard(padding: 15)
    }
}

struct EmptyPanel: View {
    let title: String
    let message: String
    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "waveform.path.ecg")
                .font(.system(size: 25, weight: .medium))
                .foregroundStyle(Brand.orange)
            Text(title).font(.headline).foregroundStyle(Brand.primaryText)
            Text(message)
                .font(.subheadline)
                .multilineTextAlignment(.center)
                .foregroundStyle(Brand.secondaryText)
        }
        .frame(maxWidth: .infinity)
        .trackCard()
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
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 15, weight: .bold, design: .rounded))
            .padding(.horizontal, 18)
            .frame(maxWidth: .infinity, minHeight: 54)
            .foregroundStyle(.black)
            .background(Brand.orange.opacity(configuration.isPressed ? 0.72 : 1))
            .clipShape(RoundedRectangle(cornerRadius: 16))
            .shadow(color: Brand.orange.opacity(configuration.isPressed ? 0.1 : 0.25), radius: 18, y: 8)
            .scaleEffect(configuration.isPressed ? 0.985 : 1)
    }
}

struct FieldBackground: ViewModifier {
    func body(content: Content) -> some View {
        content
            .padding(.horizontal, 15)
            .frame(minHeight: 54)
            .background(Brand.surface)
            .overlay(RoundedRectangle(cornerRadius: 16).stroke(Brand.line))
            .clipShape(RoundedRectangle(cornerRadius: 16))
    }
}

extension View {
    func trackField() -> some View { modifier(FieldBackground()) }
}
