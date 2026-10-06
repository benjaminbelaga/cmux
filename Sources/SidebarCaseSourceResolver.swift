import CmuxExtensionKit
import CmuxFoundation
import Foundation

/// Inert named-case source inspection. The caller retains responsibility for
/// actual workspace/binding/grant checks; this service never attaches or opens.
actor SidebarCaseSourceResolver {
    enum Failure: Error { case busy, invalidRequest, transport, invalidResponse }

    struct Binding: Codable, Equatable, Sendable {
        let caseId: String
        let sourceEventId: String
        let sourceRevision: String
        let sourceReferenceFingerprint: String
        let attachedRevision: UInt64
    }

    struct PrepareRequest: Codable, Equatable, Sendable {
        let caseID: String
        let windowID: UUID
        let workspaceID: UUID
        let expectedRevision: UInt64
        enum CodingKeys: String, CodingKey {
            case caseID = "case_id", windowID = "window_id", workspaceID = "workspace_id"
            case expectedRevision = "expected_revision"
        }
    }

    struct ResolveRequest: Codable, Equatable, Sendable {
        let windowID: UUID
        let workspaceID: UUID
        let expectedRevision: UInt64
        let caseBinding: Binding
        let sourceReference: CmuxSidebarSourceReference
        enum CodingKeys: String, CodingKey {
            case windowID = "window_id", workspaceID = "workspace_id", expectedRevision = "expected_revision"
            case caseBinding = "case_binding", sourceReference = "source_reference"
        }
    }

    struct Locator: Codable, Equatable, Sendable {
        let provider: String
        let principal: String
        /// Google tenant scope, never a native workspace UUID.
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

    struct VerifiedSource: Decodable, Equatable, Sendable {
        let status: String
        let verified: Bool
        let case_id: String
        let source_event_id: String
        let source_revision: String
        let source_reference: CmuxSidebarSourceReference
        let source_reference_fingerprint: String
        let window_id: UUID
        let workspace_id: UUID
        let expected_revision: UInt64
        let observed_at: String
        let locator: Locator
        let source_provenance: String
        let business_authority: String
        let native_provider_session_binding: Bool
        let model_calls: Int
        let sends: Int
        let drafts: Int
        let next_action: String
    }

    struct Hold: Decodable, Equatable, Sendable {
        let status: String
        let verified: Bool
        let reason: String
        let model_calls: Int
        let sends: Int
        let drafts: Int
        let next_action: String
    }

    enum Outcome: Equatable, Sendable {
        case verified(VerifiedSource)
        case held(Hold)
    }

    private let commands: any CommandRunning
    private let executable: URL
    private let temporaryDirectory: URL
    private var running = false

    init(commands: any CommandRunning = CommandRunner(maximumCaptureBytes: 65_537),
         homeDirectory: URL = FileManager.default.homeDirectoryForCurrentUser,
         temporaryDirectory: URL = FileManager.default.temporaryDirectory) {
        self.commands = commands
        self.executable = homeDirectory.appendingPathComponent(".local/share/yoyaku-case-os-source-view/current/.venv/bin/case-os")
        self.temporaryDirectory = temporaryDirectory
    }

    func prepare(_ request: PrepareRequest) async throws -> Outcome {
        guard Self.valid(request) else { throw Failure.invalidRequest }
        let data = try JSONEncoder().encode(request)
        return try Self.validate(try await execute("case-source-prepare", data: data), prepare: request)
    }

    func resolve(_ request: ResolveRequest) async throws -> Outcome {
        guard Self.valid(request) else { throw Failure.invalidRequest }
        let data = try JSONEncoder().encode(request)
        return try Self.validate(try await execute("case-source-resolve", data: data), resolve: request)
    }

    private func execute(_ command: String, data: Data) async throws -> Data {
        guard !running else { throw Failure.busy }
        guard data.count <= 8_192 else { throw Failure.invalidRequest }
        try Task.checkCancellation()
        running = true
        defer { running = false }
        let directory = temporaryDirectory.appendingPathComponent("cmux-case-source-" + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(at: directory) }
        let packet = directory.appendingPathComponent("source.json")
        guard FileManager.default.createFile(atPath: packet.path, contents: data,
            attributes: [.posixPermissions: 0o600]) else { throw Failure.transport }
        let execution = await commands.run(directory: directory.path, executable: executable.path,
            arguments: [command, packet.path], timeout: 65)
        try Task.checkCancellation()
        guard execution.executionError == nil, !execution.timedOut, execution.exitStatus == 0,
              let stdout = execution.stdout, stdout.utf8.count <= 65_536,
              (execution.stderr?.utf8.count ?? 0) <= 65_536 else { throw Failure.transport }
        return Data(stdout.utf8)
    }

    static func validate(_ data: Data, prepare request: PrepareRequest, now: Date = Date()) throws -> Outcome {
        guard valid(request) else { throw Failure.invalidRequest }
        return try validate(data, caseID: request.caseID, windowID: request.windowID,
            workspaceID: request.workspaceID, revision: request.expectedRevision, binding: nil, reference: nil, now: now)
    }

    static func validate(_ data: Data, resolve request: ResolveRequest, now: Date = Date()) throws -> Outcome {
        guard valid(request) else { throw Failure.invalidRequest }
        return try validate(data, caseID: request.caseBinding.caseId, windowID: request.windowID,
            workspaceID: request.workspaceID, revision: request.expectedRevision,
            binding: request.caseBinding, reference: request.sourceReference, now: now)
    }

    private static func validate(_ data: Data, caseID: String, windowID: UUID, workspaceID: UUID,
        revision: UInt64, binding: Binding?, reference: CmuxSidebarSourceReference?, now: Date) throws -> Outcome {
        guard data.count <= 65_536,
              let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw Failure.invalidResponse
        }
        let holdKeys: Set<String> = ["status", "verified", "reason", "model_calls", "sends", "drafts", "next_action"]
        if Set(object.keys) == holdKeys {
            let hold = try JSONDecoder().decode(Hold.self, from: data)
            guard ["source_reference_hold", "source_transport_hold"].contains(hold.status), !hold.verified,
                  matches(hold.reason, "^[a-z][a-z0-9_]{0,127}$"), safe(hold.next_action, maximum: 2_048),
                  hold.model_calls == 0, hold.sends == 0, hold.drafts == 0 else { throw Failure.invalidResponse }
            return .held(hold)
        }
        let successKeys: Set<String> = ["status", "verified", "case_id", "source_event_id", "source_revision",
            "source_reference", "source_reference_fingerprint", "window_id", "workspace_id", "expected_revision",
            "observed_at", "locator", "source_provenance", "business_authority", "native_provider_session_binding",
            "model_calls", "sends", "drafts", "next_action"]
        let locatorKeys: Set<String> = ["provider", "principal", "workspace_id", "directory_user_id", "account_ref",
            "gmail_thread_key", "gmail_thread_id", "gmail_message_id", "url"]
        let referenceKeys: Set<String> = ["provider", "accountRef", "directoryUserId", "resourceId", "messageId", "evidenceFingerprint"]
        guard Set(object.keys) == successKeys,
              let locator = object["locator"] as? [String: Any], Set(locator.keys) == locatorKeys,
              let rawReference = object["source_reference"] as? [String: Any], Set(rawReference.keys) == referenceKeys else {
            throw Failure.invalidResponse
        }
        let report = try JSONDecoder().decode(VerifiedSource.self, from: data)
        guard report.status == "verified", report.verified,
              report.case_id == caseID, report.window_id == windowID, report.workspace_id == workspaceID,
              report.expected_revision == revision, matches(report.source_event_id, "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$"),
              matches(report.source_revision, "^[a-f0-9]{64}$"), valid(report.source_reference),
              report.source_reference.fingerprint == report.source_reference_fingerprint,
              report.business_authority == "none", !report.native_provider_session_binding,
              report.model_calls == 0, report.sends == 0, report.drafts == 0,
              safe(report.source_provenance, maximum: 1_024), safe(report.next_action, maximum: 2_048) else {
            throw Failure.invalidResponse
        }
        if let binding, let reference {
            guard report.source_event_id == binding.sourceEventId, report.source_revision == binding.sourceRevision,
                  report.source_reference_fingerprint == binding.sourceReferenceFingerprint,
                  report.source_reference == reference else { throw Failure.invalidResponse }
        }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var observed = formatter.date(from: report.observed_at)
        if observed == nil {
            formatter.formatOptions = [.withInternetDateTime]
            observed = formatter.date(from: report.observed_at)
        }
        guard let observed, (-5...120).contains(now.timeIntervalSince(observed)) else { throw Failure.invalidResponse }
        let result = report.locator
        guard result.provider == "gmail", result.directoryUserID == report.source_reference.directoryUserId,
              result.accountRef == report.source_reference.accountRef, result.gmailThreadKey == report.source_reference.resourceId,
              result.gmailMessageID == report.source_reference.messageId,
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
        return .verified(report)
    }

    private static func valid(_ request: PrepareRequest) -> Bool {
        matches(request.caseID, "^CASE-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9A-F]{8}$")
    }

    private static func valid(_ request: ResolveRequest) -> Bool {
        let binding = request.caseBinding
        return matches(binding.caseId, "^CASE-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9A-F]{8}$")
            && matches(binding.sourceEventId, "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
            && matches(binding.sourceRevision, "^[a-f0-9]{64}$") && valid(request.sourceReference)
            && request.sourceReference.fingerprint == binding.sourceReferenceFingerprint
            && binding.attachedRevision <= request.expectedRevision
    }

    private static func valid(_ reference: CmuxSidebarSourceReference) -> Bool {
        reference.isStructurallyValid && reference.messageId != nil
            && [reference.provider, reference.accountRef, reference.directoryUserId, reference.resourceId,
                reference.messageId ?? "", reference.evidenceFingerprint]
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
