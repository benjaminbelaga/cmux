import CmuxExtensionKit
import CmuxFoundation
import Foundation

/// Read-only canonical resolver. A returned locator still requires the native
/// caller's current workspace/binding check; this component never opens it.
actor SidebarSourceReferenceResolver {
    enum Failure: Error { case busy, invalidRequest, transport, invalidResponse, held }
    struct Request: Codable, Equatable, Sendable {
        let requestID: String
        let workspaceID: UUID
        let expectedRevision: UInt64
        let sourceReference: CmuxSidebarSourceReference
        enum CodingKeys: String, CodingKey {
            case requestID = "request_id", workspaceID = "workspace_id"
            case expectedRevision = "expected_revision", sourceReference = "source_reference"
        }
    }
    struct Locator: Codable, Equatable, Sendable {
        let provider: String
        let principal: String
        let workspaceID: String
        let directoryUserID: String
        let accountRef: String
        let gmailThreadKey: String
        let gmailThreadID: String
        let gmailMessageID: String
        let url: String
        enum CodingKeys: String, CodingKey {
            case provider, principal, url
            case workspaceID = "workspace_id", directoryUserID = "directory_user_id"
            case accountRef = "account_ref", gmailThreadKey = "gmail_thread_key"
            case gmailThreadID = "gmail_thread_id", gmailMessageID = "gmail_message_id"
        }
    }
    private struct Report: Decodable {
        let status: String
        let verified: Bool
        let request_id: String
        let workspace_id: UUID
        let expected_revision: UInt64
        let observed_at: String
        let source_reference: CmuxSidebarSourceReference
        let locator: Locator
        let business_authority: String
        let native_provider_session_binding: Bool
        let model_calls: Int
        let sends: Int
        let drafts: Int
    }
    private let commands: any CommandRunning
    private let executable: URL
    private let temporaryDirectory: URL
    private var running = false

    init(commands: any CommandRunning = CommandRunner(maximumCaptureBytes: 65_537),
         homeDirectory: URL = FileManager.default.homeDirectoryForCurrentUser,
         temporaryDirectory: URL = FileManager.default.temporaryDirectory) {
        self.commands = commands
        self.executable = homeDirectory.appendingPathComponent(".local/share/yoyaku-case-os-client/current/.venv/bin/case-os")
        self.temporaryDirectory = temporaryDirectory
    }

    /// Uses the existing supported payload-file rail, avoiding shell/stdin
    /// wrappers. Input is private and removed after the canonical runner finishes.
    func resolve(_ request: Request) async throws -> Locator {
        guard !running else { throw Failure.busy }
        guard Self.valid(request) else { throw Failure.invalidRequest }
        let data = try JSONEncoder().encode(request)
        guard data.count <= 8_192 else { throw Failure.invalidRequest }
        running = true
        defer { running = false }
        try Task.checkCancellation()
        let directory = temporaryDirectory.appendingPathComponent("cmux-source-reference-" + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(at: directory) }
        let packet = directory.appendingPathComponent("source.json")
        guard FileManager.default.createFile(atPath: packet.path, contents: data,
            attributes: [.posixPermissions: 0o600]) else { throw Failure.transport }
        let execution = await commands.run(directory: directory.path, executable: executable.path,
            arguments: ["mission-source-resolve", packet.path], timeout: 20)
        try Task.checkCancellation()
        guard execution.executionError == nil, !execution.timedOut, execution.exitStatus == 0,
              let stdout = execution.stdout, stdout.utf8.count <= 65_536,
              (execution.stderr?.utf8.count ?? 0) <= 65_536 else { throw Failure.transport }
        return try Self.validate(Data(stdout.utf8), request: request)
    }

    static func validate(_ data: Data, request: Request, now: Date = Date()) throws -> Locator {
        guard valid(request), data.count <= 65_536 else { throw Failure.invalidRequest }
        let raw = try JSONSerialization.jsonObject(with: data)
        guard let object = raw as? [String: Any] else { throw Failure.invalidResponse }
        let successKeys: Set<String> = ["status", "verified", "request_id", "workspace_id", "expected_revision",
            "observed_at", "source_reference", "locator", "source_provenance", "business_authority",
            "native_provider_session_binding", "model_calls", "sends", "drafts", "next_action"]
        let locatorKeys: Set<String> = ["provider", "principal", "workspace_id", "directory_user_id", "account_ref",
            "gmail_thread_key", "gmail_thread_id", "gmail_message_id", "url"]
        guard Set(object.keys) == successKeys,
              let locator = object["locator"] as? [String: Any], Set(locator.keys) == locatorKeys,
              let provenance = object["source_provenance"] as? String, safe(provenance, maximum: 1_024),
              let nextAction = object["next_action"] as? String, safe(nextAction, maximum: 2_048) else {
            throw Failure.invalidResponse
        }
        let report = try JSONDecoder().decode(Report.self, from: data)
        guard report.status == "verified", report.verified,
              report.request_id == request.requestID, report.workspace_id == request.workspaceID,
              report.expected_revision == request.expectedRevision, report.source_reference == request.sourceReference,
              report.business_authority == "none", !report.native_provider_session_binding,
              report.model_calls == 0, report.sends == 0, report.drafts == 0 else { throw Failure.invalidResponse }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var observed = formatter.date(from: report.observed_at)
        if observed == nil {
            formatter.formatOptions = [.withInternetDateTime]
            observed = formatter.date(from: report.observed_at)
        }
        guard let observed, (-5...120).contains(now.timeIntervalSince(observed)) else { throw Failure.invalidResponse }
        let result = report.locator
        guard result.provider == "gmail", result.directoryUserID == request.sourceReference.directoryUserId,
              result.accountRef == request.sourceReference.accountRef,
              result.gmailThreadKey == request.sourceReference.resourceId,
              result.gmailMessageID == request.sourceReference.messageId,
              matches(result.gmailThreadID, "^[A-Za-z0-9_-]{1,256}$"),
              matches(result.workspaceID, "^[A-Za-z0-9.-]{1,253}$"),
              matches(result.principal, "^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9.-]{1,253}$") else {
            throw Failure.invalidResponse
        }
        let unreserved = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
        guard let principal = result.principal.addingPercentEncoding(withAllowedCharacters: unreserved),
              let thread = result.gmailThreadID.addingPercentEncoding(withAllowedCharacters: unreserved),
              result.url == "https://mail.google.com/mail/?authuser=" + principal + "#all/" + thread else {
            throw Failure.invalidResponse
        }
        return result
    }

    private static func valid(_ request: Request) -> Bool {
        matches(request.requestID, "^CEO-[0-9a-f]{32}$") && request.sourceReference.isStructurallyValid
            && request.sourceReference.messageId != nil
            && [request.sourceReference.provider, request.sourceReference.accountRef,
                request.sourceReference.directoryUserId, request.sourceReference.resourceId,
                request.sourceReference.messageId ?? "", request.sourceReference.evidenceFingerprint]
                .allSatisfy { safe($0, maximum: 256) && !$0.contains(where: { $0.isWhitespace }) }
    }
    private static func matches(_ value: String, _ pattern: String) -> Bool {
        value.range(of: pattern, options: .regularExpression) == value.startIndex..<value.endIndex
    }
    private static func safe(_ value: String, maximum: Int) -> Bool {
        !value.isEmpty && value.utf8.count <= maximum
            && !value.unicodeScalars.contains { CharacterSet.controlCharacters.contains($0) }
    }
}
