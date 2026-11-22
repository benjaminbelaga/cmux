public import Foundation

/// Selects the concrete working directory for a newly created workspace.
public struct WorkspaceCreationWorkingDirectoryPolicy: Sendable {
    private let inheritanceEnabled: Bool

    /// Creates a policy for the current workspace inheritance preference.
    public init(inheritanceEnabled: Bool) {
        self.inheritanceEnabled = inheritanceEnabled
    }

    /// Applies workspace-creation precedence without allowing disabled
    /// inheritance to collapse into an ambiguous `nil` terminal override.
    public func resolve(
        explicitWorkingDirectory: String?,
        inheritedWorkingDirectory: String?,
        defaultWorkingDirectory: @autoclosure () -> String
    ) -> String {
        if let explicitWorkingDirectory = normalized(explicitWorkingDirectory) {
            return explicitWorkingDirectory
        }
        if inheritanceEnabled,
           let inheritedWorkingDirectory = normalized(inheritedWorkingDirectory) {
            return inheritedWorkingDirectory
        }
        return normalized(defaultWorkingDirectory()) ?? "/"
    }

    /// Allocates a workspace-specific directory without changing an existing entry.
    public static func createNeutralDirectory(
        under home: URL,
        workspaceID: UUID,
        fileManager: FileManager = .default
    ) throws -> URL {
        let root = neutralRoot(under: home)
        let permissions: [FileAttributeKey: Any] = [.posixPermissions: 0o700]
        do {
            try fileManager.createDirectory(at: root, withIntermediateDirectories: false, attributes: permissions)
        } catch let error as CocoaError where error.code == .fileWriteFileExists {
            // Another workspace may already have created the common parent.
        }
        let attributes = try fileManager.attributesOfItem(atPath: root.path)
        guard attributes[.type] as? FileAttributeType == .typeDirectory,
              (attributes[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(),
              (attributes[.posixPermissions] as? NSNumber)?.intValue == 0o700 else {
            throw CocoaError(.fileWriteNoPermission, userInfo: [NSFilePathErrorKey: root.path])
        }
        let name = "session-\(workspaceID.uuidString.lowercased())"
        let directory = root.appendingPathComponent(name, isDirectory: true)
        try fileManager.createDirectory(at: directory, withIntermediateDirectories: false, attributes: permissions)
        return directory
    }

    /// Parent of the isolated workspace directories for this account.
    public static func neutralRoot(under home: URL) -> URL {
        home.appendingPathComponent("sessions", isDirectory: true)
    }

    private func normalized(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}
