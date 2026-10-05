import CmuxFoundation
import Foundation

/// Fresh readback from immutable classifier code and the current registered rules.
/// The engine URL is composed by the native owner, never supplied by a plan.
actor SidebarOrganizationRegistryReader {
    enum Failure: Error { case busy, pythonUnavailable, engineFailed, invalidOutput }
    private let commands: any CommandRunning
    private let engineURL: URL
    private let rulesURL: URL
    private let pythonCandidates: [String]
    private var running = false

    init(engineURL: URL, rulesURL: URL? = nil,
         homeDirectory: URL = FileManager.default.homeDirectoryForCurrentUser,
         commands: any CommandRunning = CommandRunner(maximumCaptureBytes: 65_537),
         pythonCandidates: [String] = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]) {
        self.engineURL = engineURL
        self.rulesURL = rulesURL ?? homeDirectory.appendingPathComponent("repos/ecosystem/inventory/session-organization.yaml")
        self.commands = commands
        self.pythonCandidates = pythonCandidates
    }

    func read() async throws -> String {
        guard !running else { throw Failure.busy }
        running = true
        defer { running = false }
        try Task.checkCancellation()
        var python: String?
        for candidate in pythonCandidates {
            let result = await commands.run(directory: rulesURL.deletingLastPathComponent().path,
                executable: candidate, arguments: ["-c", "import sys,yaml; assert sys.version_info >= (3,11)"], timeout: 3)
            try Task.checkCancellation()
            if validExecution(result) { python = candidate; break }
        }
        guard let python else { throw Failure.pythonUnavailable }
        let result = await commands.run(directory: rulesURL.deletingLastPathComponent().path,
            executable: python, arguments: [engineURL.path, "--registry-fingerprint", "--rules", rulesURL.path], timeout: 20)
        try Task.checkCancellation()
        guard validExecution(result), let output = result.stdout,
              let data = output.data(using: .utf8) else { throw Failure.engineFailed }
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              Set(object.keys) == Set(["schemaVersion", "authority", "registryFingerprint"]),
              let schema = object["schemaVersion"] as? NSNumber, CFGetTypeID(schema) != CFBooleanGetTypeID(), schema == 1,
              object["authority"] as? String == "none",
              let fingerprint = object["registryFingerprint"] as? String,
              fingerprint.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil else { throw Failure.invalidOutput }
        return fingerprint
    }

    private func validExecution(_ result: CommandResult) -> Bool {
        result.executionError == nil && !result.timedOut && result.exitStatus == 0
            && (result.stdout?.utf8.count ?? 0) <= 65_536 && (result.stderr?.utf8.count ?? 0) <= 65_536
    }
}
