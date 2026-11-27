import CmuxWorkspaces
import Foundation
import Testing

@Suite struct WorkspaceCreationWorkingDirectoryPolicyTests {
    @Test func explicitDirectoryWins() {
        #expect(
            resolve(explicit: "/explicit", inherited: "/inherited", enabled: true)
                == "/explicit"
        )
    }

    @Test func enabledInheritanceUsesSourceDirectory() {
        #expect(
            resolve(explicit: nil, inherited: "/inherited", enabled: true)
                == "/inherited"
        )
    }

    @Test func disabledInheritanceUsesConcreteDefault() {
        #expect(resolve(explicit: nil, inherited: "/inherited", enabled: false) == "/default")
    }

    @Test func missingInheritedDirectoryUsesConcreteDefault() {
        #expect(resolve(explicit: nil, inherited: nil, enabled: true) == "/default")
    }

    @Test func namedDirectoryIsPrivateAndCannotBeReused() throws {
        let home = try temporaryHome()
        defer { try? FileManager.default.removeItem(at: home) }
        let workspaceID = UUID()
        let first = try WorkspaceCreationWorkingDirectoryPolicy.createNeutralDirectory(
            under: home, workspaceID: workspaceID
        )
        let second = try WorkspaceCreationWorkingDirectoryPolicy.createNeutralDirectory(
            under: home, workspaceID: UUID()
        )
        #expect(first.lastPathComponent == "session-\(workspaceID.uuidString.lowercased())")
        #expect(first.path != second.path)
        let mode = try FileManager.default.attributesOfItem(atPath: first.path)[.posixPermissions] as? NSNumber
        #expect(mode?.intValue == 0o700)
        #expect(throws: CocoaError.self) {
            try WorkspaceCreationWorkingDirectoryPolicy.createNeutralDirectory(under: home, workspaceID: workspaceID)
        }
    }

    @Test func explicitTitleIsReflectedInSafeDirectoryName() throws {
        let home = try temporaryHome()
        defer { try? FileManager.default.removeItem(at: home) }
        let workspaceID = UUID()
        let directory = try WorkspaceCreationWorkingDirectoryPolicy.createNeutralDirectory(
            under: home, workspaceID: workspaceID, title: "Préparer / Livraison"
        )
        #expect(directory.lastPathComponent == "preparer-livraison-\(workspaceID.uuidString.lowercased())")
    }

    @Test func symlinkParentCannotBecomeNeutralRoot() throws {
        let home = try temporaryHome()
        defer { try? FileManager.default.removeItem(at: home) }
        let parent = WorkspaceCreationWorkingDirectoryPolicy.neutralRoot(under: home)
        try FileManager.default.createSymbolicLink(at: parent, withDestinationURL: home)
        #expect(throws: CocoaError.self) {
            try WorkspaceCreationWorkingDirectoryPolicy.createNeutralDirectory(under: home, workspaceID: UUID())
        }
    }

    private func temporaryHome() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: false)
        return url
    }

    private func resolve(explicit: String?, inherited: String?, enabled: Bool) -> String {
        WorkspaceCreationWorkingDirectoryPolicy(inheritanceEnabled: enabled).resolve(
            explicitWorkingDirectory: explicit,
            inheritedWorkingDirectory: inherited,
            defaultWorkingDirectory: "/default"
        )
    }
}
