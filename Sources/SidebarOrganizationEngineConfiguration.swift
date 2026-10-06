import CoreFoundation
import CryptoKit
import Darwin
import Foundation

/// One configuration binds both consumers to immutable code and reviewed rules.
/// The four-key legacy configuration continues to read canonical rules; a
/// supplied fifth key must bind the private generated projection without fallback.
struct SidebarOrganizationEngineConfiguration: Sendable {
    let engineURL: URL
    let rulesURL: URL
    private let engineSHA256: String
    private let sourceCommit: String
    private let homeDirectory: URL
    private let rulesSHA256: String?
    private let receiptSHA256: String?

    init?(bundle: Bundle = .main,
          homeDirectory: URL = FileManager.default.homeDirectoryForCurrentUser) {
        guard let bundleID = bundle.bundleIdentifier,
              bundleID.hasPrefix("com.cmuxterm.app.debug."),
              let environment = bundle.object(forInfoDictionaryKey: "LSEnvironment") as? [String: String],
              let source = environment["CMUX_ORGANIZATION_ENGINE_SOURCE"],
              source.utf8.count == 40, Self.hexadecimal(source),
              let fingerprint = environment["CMUX_ORGANIZATION_ENGINE_SHA256"],
              fingerprint.utf8.count == 64, Self.hexadecimal(fingerprint) else { return nil }
        let runtime = homeDirectory.appendingPathComponent(".local/share/cmux-session-organization", isDirectory: true)
        let engine = runtime.appendingPathComponent(source, isDirectory: true).appendingPathComponent("session-organization.py")
        guard environment["CMUX_ORGANIZATION_ENGINE_PATH"] == engine.path,
              Self.validEngine(engine, fingerprint: fingerprint) else { return nil }

        let rules: URL
        let receiptFingerprint: String?
        let projectedFingerprint = environment["CMUX_ORGANIZATION_RULES_SHA256"]
        if let projectedFingerprint {
            guard projectedFingerprint.utf8.count == 64, Self.hexadecimal(projectedFingerprint) else { return nil }
            rules = runtime.appendingPathComponent("rules", isDirectory: true)
                .appendingPathComponent(source + "-" + projectedFingerprint, isDirectory: true)
                .appendingPathComponent("session-organization.yaml")
            guard environment["CMUX_ORGANIZATION_RULES_PATH"] == rules.path,
                  let receipt = Self.validProjectedRules(rules, source: source,
                      rulesFingerprint: projectedFingerprint, engineFingerprint: fingerprint,
                      homeDirectory: homeDirectory) else { return nil }
            receiptFingerprint = receipt
        } else {
            rules = homeDirectory.appendingPathComponent("repos/ecosystem/inventory/session-organization.yaml")
            guard environment["CMUX_ORGANIZATION_RULES_PATH"] == rules.path else { return nil }
            receiptFingerprint = nil
        }
        engineURL = engine
        rulesURL = rules
        engineSHA256 = fingerprint
        sourceCommit = source
        self.homeDirectory = homeDirectory
        rulesSHA256 = projectedFingerprint
        receiptSHA256 = receiptFingerprint
    }

    /// Called before and after each real analysis/registry invocation. Mutable
    /// registered facts stay fresh; code, projection and receipt cannot drift.
    func isCurrent() -> Bool {
        guard Self.validEngine(engineURL, fingerprint: engineSHA256) else { return false }
        guard let rulesSHA256 else { return true }
        return Self.validProjectedRules(rulesURL, source: sourceCommit,
            rulesFingerprint: rulesSHA256, engineFingerprint: engineSHA256,
            homeDirectory: homeDirectory) == receiptSHA256
    }

    private static func validEngine(_ engine: URL, fingerprint: String) -> Bool {
        guard privateDirectory(engine.deletingLastPathComponent()),
              let data = privateData(engine, maximumBytes: 2 * 1024 * 1024) else { return false }
        return sha256(data) == fingerprint
    }

    /// Return the byte fingerprint so later validation also fences receipt edits
    /// that preserve decoded fields. This generated receipt grants no authority.
    private static func validProjectedRules(_ rules: URL, source: String,
                                            rulesFingerprint: String, engineFingerprint: String,
                                            homeDirectory: URL) -> String? {
        let directory = rules.deletingLastPathComponent()
        let rulesRoot = directory.deletingLastPathComponent()
        let runtime = rulesRoot.deletingLastPathComponent()
        guard [runtime, rulesRoot, directory].allSatisfy(privateDirectory),
              let data = privateData(rules, maximumBytes: 256 * 1024),
              sha256(data) == rulesFingerprint,
              let receiptData = privateData(directory.appendingPathComponent("receipt.json"), maximumBytes: 16 * 1024),
              let receipt = try? JSONSerialization.jsonObject(with: receiptData) as? [String: Any],
              Set(receipt.keys) == Set(["schemaVersion", "authority", "sourceCommit", "sourceYamlSHA256",
                  "projectionSHA256", "engineSHA256", "canonicalFactsRoot"]),
              let version = receipt["schemaVersion"] as? NSNumber,
              CFGetTypeID(version) != CFBooleanGetTypeID(), version == 1,
              receipt["authority"] as? String == "none",
              receipt["sourceCommit"] as? String == source,
              receipt["projectionSHA256"] as? String == rulesFingerprint,
              receipt["engineSHA256"] as? String == engineFingerprint,
              receipt["canonicalFactsRoot"] as? String == homeDirectory.appendingPathComponent("repos/ecosystem").path,
              let sourceFingerprint = receipt["sourceYamlSHA256"] as? String,
              sourceFingerprint.utf8.count == 64, hexadecimal(sourceFingerprint) else { return nil }
        return sha256(receiptData)
    }

    private static func privateDirectory(_ directory: URL) -> Bool {
        guard directory.resolvingSymlinksInPath().path == directory.path,
              let attributes = try? FileManager.default.attributesOfItem(atPath: directory.path),
              attributes[.type] as? FileAttributeType == .typeDirectory,
              (attributes[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(),
              (attributes[.posixPermissions] as? NSNumber)?.intValue == 0o700 else { return false }
        return true
    }

    /// Bound reads on a verified descriptor: a changed file cannot evade the
    /// size/mode checks between path inspection and reading. Ancestor symlinks
    /// are rejected as well as final-component symlinks.
    private static func privateData(_ file: URL, maximumBytes: Int) -> Data? {
        guard file.resolvingSymlinksInPath().path == file.path else { return nil }
        let descriptor = open(file.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        guard descriptor >= 0 else { return nil }
        let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        defer { try? handle.close() }
        var info = stat()
        guard fstat(descriptor, &info) == 0,
              info.st_mode & S_IFMT == S_IFREG, info.st_uid == getuid(),
              info.st_mode & 0o7777 == 0o600,
              info.st_size >= 0, info.st_size <= Int64(maximumBytes),
              let data = try? handle.read(upToCount: maximumBytes + 1),
              data.count <= maximumBytes,
              file.resolvingSymlinksInPath().path == file.path else { return nil }
        return data
    }

    private static func sha256(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    private static func hexadecimal(_ value: String) -> Bool {
        value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }
}
