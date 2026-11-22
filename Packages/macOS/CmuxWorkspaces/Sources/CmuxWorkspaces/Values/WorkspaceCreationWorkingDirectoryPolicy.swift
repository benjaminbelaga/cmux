public import Foundation
internal import Darwin

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
    public static func createNeutralDirectory(under home: URL, workspaceID: UUID, title: String? = nil) throws -> URL {
        let root = neutralRoot(under: home)
        let privateMode: mode_t = 0o700
        if mkdir(root.path, privateMode) != 0, errno != EEXIST {
            throw CocoaError(.fileWriteNoPermission, userInfo: [NSFilePathErrorKey: root.path])
        }
        let parent = open(root.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard parent >= 0 else {
            throw CocoaError(.fileWriteNoPermission, userInfo: [NSFilePathErrorKey: root.path])
        }
        defer { _ = close(parent) }

        var parentInfo = stat()
        guard fstat(parent, &parentInfo) == 0,
              parentInfo.st_uid == getuid(), parentInfo.st_mode & 0o777 == privateMode else {
            throw CocoaError(.fileWriteNoPermission, userInfo: [NSFilePathErrorKey: root.path])
        }

        let label = title?.folding(options: .diacriticInsensitive, locale: Locale(identifier: "en_US_POSIX"))
            .lowercased()
            .split(whereSeparator: { !$0.isASCII || (!$0.isLetter && !$0.isNumber) })
            .joined(separator: "-")
        let prefix = String((label ?? "").prefix(28)).trimmingCharacters(in: CharacterSet(charactersIn: "-"))
        let name = "\(prefix.isEmpty ? "session" : prefix)-\(workspaceID.uuidString.lowercased())"
        let directory = root.appendingPathComponent(name, isDirectory: true)
        if mkdirat(parent, name, privateMode) != 0 {
            let code: CocoaError.Code = errno == EEXIST ? .fileWriteFileExists : .fileWriteNoPermission
            throw CocoaError(code, userInfo: [NSFilePathErrorKey: directory.path])
        }
        var namedParentInfo = stat()
        guard lstat(root.path, &namedParentInfo) == 0,
              namedParentInfo.st_dev == parentInfo.st_dev, namedParentInfo.st_ino == parentInfo.st_ino else {
            throw CocoaError(.fileWriteNoPermission, userInfo: [NSFilePathErrorKey: root.path])
        }
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
