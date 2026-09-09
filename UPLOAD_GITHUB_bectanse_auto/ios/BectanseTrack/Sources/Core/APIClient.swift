import Foundation

struct APIError: LocalizedError {
    let statusCode: Int
    let message: String
    var errorDescription: String? { message }
}

actor APIClient {
    static let shared = APIClient()
    private let baseURL = URL(string: "https://acces.bectanse-academie.com")!
    private let session: URLSession
    private let decoder: JSONDecoder
    private let encoder: JSONEncoder

    init() {
        let configuration = URLSessionConfiguration.default
        configuration.httpCookieStorage = .shared
        configuration.httpShouldSetCookies = true
        configuration.waitsForConnectivity = true
        configuration.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        configuration.timeoutIntervalForRequest = 25
        configuration.timeoutIntervalForResource = 45
        session = URLSession(configuration: configuration)
        decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
    }

    func get<T: Decodable>(_ path: String, query: [URLQueryItem] = []) async throws -> T {
        try await request(path, method: "GET", query: query, body: Optional<EmptyBody>.none)
    }

    func post<T: Decodable, Body: Encodable>(_ path: String, body: Body) async throws -> T {
        try await request(path, method: "POST", body: body)
    }

    func delete<T: Decodable, Body: Encodable>(_ path: String, body: Body) async throws -> T {
        try await request(path, method: "DELETE", body: body)
    }

    private func request<T: Decodable, Body: Encodable>(
        _ path: String,
        method: String,
        query: [URLQueryItem] = [],
        body: Body?
    ) async throws -> T {
        guard var components = URLComponents(url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false) else {
            throw APIError(statusCode: 0, message: "Adresse du service invalide.")
        }
        if !query.isEmpty { components.queryItems = query }
        guard let url = components.url else {
            throw APIError(statusCode: 0, message: "Adresse du service invalide.")
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("BectanseTrack-iOS/1", forHTTPHeaderField: "X-Bectanse-Client")
        if let body {
            request.httpBody = try encoder.encode(body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, rawResponse) = try await session.data(for: request)
        guard let response = rawResponse as? HTTPURLResponse else {
            throw APIError(statusCode: 0, message: "Réponse réseau invalide.")
        }
        guard 200..<300 ~= response.statusCode else {
            let server = try? decoder.decode(MessageResponse.self, from: data)
            let fallback = response.statusCode == 401
                ? "Votre session a expiré. Reconnectez-vous."
                : "Le service est momentanément indisponible."
            throw APIError(statusCode: response.statusCode, message: server?.error ?? fallback)
        }
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw APIError(statusCode: response.statusCode, message: "La réponse reçue est incomplète.")
        }
    }
}

private struct EmptyBody: Encodable {}

struct CodeLoginBody: Encodable { let code: String }
struct TrialBody: Encodable { let name: String; let email: String }
struct ConnectAccountBody: Encodable {
    let platform = "MT5"
    let displayName: String
    let broker: String
    let server: String
    let login: String
    let password: String
}
struct DeleteAccountBody: Encodable { let mode: String }
