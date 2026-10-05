import CryptoKit
import Foundation

/// The isolated signed pair declares immutable engine code and current rules.
/// Missing or invalid configuration leaves legacy tag analysis available;
/// it cannot enable a typed folder plan.
struct SidebarOrganizationEngineConfiguration {
    let engineURL: URL
    let rulesURL: URL

    init?(bundle: Bundle = .main,
          homeDirectory: URL = FileManager.default.homeDirectoryForCurrentUser) {
        guard let bundleID = bundle.bundleIdentifier,
              bundleID.hasPrefix("com.cmuxterm.app.debug."),
              let environment = bundle.object(forInfoDictionaryKey: "LSEnvironment") as? [String: String],
              let source = environment["CMUX_ORGANIZATION_ENGINE_SOURCE"],
              source.utf8.count == 40, Self.hexadecimal(source),
              let fingerprint = environment["CMUX_ORGANIZATION_ENGINE_SHA256"],
              fingerprint.utf8.count == 64, Self.hexadecimal(fingerprint) else { return nil }
        let directory = homeDirectory.appendingPathComponent(".local/share/cmux-session-organization/" + source, isDirectory: true)
        let engine = directory.appendingPathComponent("session-organization.py")
        let rules = homeDirectory.appendingPathComponent("repos/ecosystem/inventory/session-organization.yaml")
        guard environment["CMUX_ORGANIZATION_ENGINE_PATH"] == engine.path,
              environment["CMUX_ORGANIZATION_RULES_PATH"] == rules.path,
              let directoryAttributes = try? FileManager.default.attributesOfItem(atPath: directory.path),
              directoryAttributes[.type] as? FileAttributeType == .typeDirectory,
              (directoryAttributes[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(),
              (directoryAttributes[.posixPermissions] as? NSNumber)?.intValue == 0o700,
              let attributes = try? FileManager.default.attributesOfItem(atPath: engine.path),
              attributes[.type] as? FileAttributeType == .typeRegular,
              (attributes[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(),
              (attributes[.posixPermissions] as? NSNumber)?.intValue == 0o600,
              ((attributes[.size] as? NSNumber)?.intValue ?? Int.max) <= 2 * 1024 * 1024,
              engine.resolvingSymlinksInPath().path == engine.path,
              let data = try? Data(contentsOf: engine),
              SHA256.hash(data: data).map({ String(format: "%02x", $0) }).joined() == fingerprint else { return nil }
        engineURL = engine
        rulesURL = rules
    }

    private static func hexadecimal(_ value: String) -> Bool {
        value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }
}
