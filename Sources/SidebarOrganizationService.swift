import CmuxFoundation
import Foundation

/// Runs the canonical engine locally, without requiring the Cortex application or a model subscription.
actor SidebarOrganizationService: SidebarOrganizationAnalyzing {
    enum Failure: Error { case busy, invalidInput, pythonUnavailable, engineFailed, invalidOutput }
    private let commands: any CommandRunning
    private let engineURL: URL
    private let rulesURL: URL
    private let temporaryDirectory: URL
    private let contextReader: SidebarOrganizationContextReader
    private let pythonCandidates: [String]
    private let engineValidation: (@Sendable () -> Bool)?
    private var isRunning = false

    init(commands: any CommandRunning = CommandRunner(maximumCaptureBytes: 65_537),
         homeDirectory: URL = FileManager.default.homeDirectoryForCurrentUser,
         temporaryDirectory: URL = FileManager.default.temporaryDirectory,
         engineURL: URL? = nil,
         rulesURL: URL? = nil,
         engineValidation: (@Sendable () -> Bool)? = nil,
         pythonCandidates: [String] = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]) {
        self.commands = commands
        self.engineURL = engineURL ?? homeDirectory.appendingPathComponent("repos/ecosystem/scripts/session-organization.py")
        self.rulesURL = rulesURL ?? homeDirectory.appendingPathComponent("repos/ecosystem/inventory/session-organization.yaml")
        self.temporaryDirectory = temporaryDirectory
        self.pythonCandidates = pythonCandidates
        self.engineValidation = engineValidation
        self.contextReader = SidebarOrganizationContextReader(homeDirectory: homeDirectory)
    }

    func prepare(_ input: SidebarOrganizationInput) async throws -> SidebarOrganizationInput {
        try Task.checkCancellation()
        var enriched = input
        for index in enriched.workspaces.indices {
            try Task.checkCancellation()
            var remaining = 12_000
            remaining -= enriched.workspaces[index].boundEnrichment(maximumCharacters: remaining,
                homeDirectory: contextReader.homeDirectory)
            for session in enriched.workspaces[index].sessions.indices {
                try Task.checkCancellation()
                let current = enriched.workspaces[index].sessions[session]
                var context = current.context?.bounded(maximumCharacters: min(6_000, remaining),
                    homeDirectory: contextReader.homeDirectory)
                    ?? contextReader.context(for: current, maximumCharacters: min(6_000, remaining))
                if remaining == 0 { context.contextStatus = .metadataOnly }
                else if context.contextStatus == nil { context.contextStatus = context.characterCount > 0 ? .observed : .metadataOnly }
                enriched.workspaces[index].sessions[session].context = context
                remaining = max(0, remaining - context.characterCount)
            }
        }
        return enriched
    }

    func analyze(_ input: SidebarOrganizationInput, review: Data? = nil) async throws -> SidebarOrganizationOutput {
        guard !isRunning else { throw Failure.busy }
        guard input.isValid, (review?.count ?? 0) <= 2 * 1024 * 1024 else { throw Failure.invalidInput }
        isRunning = true
        defer { isRunning = false }
        try Task.checkCancellation()
        guard engineValidation?() != false else { throw Failure.engineFailed }
        let directory = temporaryDirectory.appendingPathComponent("cmux-organization-" + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(at: directory) }
        let inputURL = directory.appendingPathComponent("input.json")
        let outputURL = directory.appendingPathComponent("output.json")
        let enriched = try await prepare(input)
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        let data = try encoder.encode(enriched)
        guard data.count <= 2 * 1024 * 1024 else { throw Failure.invalidInput }
        try data.write(to: inputURL, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: inputURL.path)
        var python: String?
        for candidate in pythonCandidates {
            let check = await commands.run(directory: directory.path, executable: candidate,
                arguments: ["-c", "import sys,yaml; assert sys.version_info >= (3,11)"], timeout: 3)
            try Task.checkCancellation()
            if check.executionError == nil, !check.timedOut, check.exitStatus == 0,
               (check.stdout?.utf8.count ?? 0) <= 65_536, (check.stderr?.utf8.count ?? 0) <= 65_536 { python = candidate; break }
        }
        guard let python else { throw Failure.pythonUnavailable }
        var arguments = [engineURL.path, "--rules", rulesURL.path, "--input", inputURL.path, "--output", outputURL.path]
        if let review {
            let reviewURL = directory.appendingPathComponent("review.json")
            try review.write(to: reviewURL, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: reviewURL.path)
            arguments += ["--review", reviewURL.path]
        }
        guard engineValidation?() != false else { throw Failure.engineFailed }
        let execution = await commands.run(directory: directory.path, executable: python, arguments: arguments, timeout: 20)
        try Task.checkCancellation()
        guard engineValidation?() != false, execution.executionError == nil, !execution.timedOut, execution.exitStatus == 0,
              (execution.stdout?.utf8.count ?? 0) <= 65_536, (execution.stderr?.utf8.count ?? 0) <= 65_536 else { throw Failure.engineFailed }
        let values = try outputURL.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
        guard values.isRegularFile == true, values.isSymbolicLink != true,
              (values.fileSize ?? Int.max) <= 2 * 1024 * 1024 else { throw Failure.invalidOutput }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        let result = try decoder.decode(SidebarOrganizationOutput.self, from: Data(contentsOf: outputURL))
        guard result.registryFingerprint == nil || result.registryFingerprint.map({ value in
                value.utf8.count == 64 && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
              }) == true,
              result.schemaVersion == 1, result.proposals.count <= input.workspaces.count,
              Set(result.proposals.map(\.workspaceId)).count == result.proposals.count,
              result.proposals.allSatisfy({ proposal in
                  guard let workspace = input.workspaces.first(where: { $0.id == proposal.workspaceId }) else { return false }
                  return proposal.expectedRevision == workspace.revision
                      && !workspace.rejectedSourceFingerprints.contains(proposal.sourceFingerprint)
                      && Set(proposal.conversationIDs).isSubset(of: Set(workspace.sessions.map(\.sessionId)))
                      && proposal.isValid
              }) else { throw Failure.invalidOutput }
        if review != nil, result.diagnostics.contains(where: { ["stale-semantic-review-refused", "invalid-semantic-review"].contains($0.code) }) { throw Failure.invalidOutput }
        return result
    }
}
