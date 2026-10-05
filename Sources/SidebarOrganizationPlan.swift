import CryptoKit
import Foundation

/// A reviewed, navigation-only folder change bound to an exact native inventory.
struct SidebarOrganizationPlan: Codable, Equatable, Sendable {
    struct Workspace: Codable, Equatable, Sendable {
        let id: UUID
        let revision: UInt64
        let title: String
        let metadataFingerprint: String
        var groupID: UUID?
        let generatedAnchor: Bool
    }

    struct Group: Codable, Equatable, Sendable {
        let id: UUID
        let name: String
        let externalID: String?
        let anchorID: UUID?
        let generatedAnchor: Bool
        let pinned: Bool
        let collapsed: Bool
        let metadataFingerprint: String
        var members: [UUID]
    }

    struct Inventory: Codable, Equatable, Sendable {
        let windowID: UUID
        var workspaces: [Workspace]
        var groups: [Group]
        var order: [UUID]
        let selectionFingerprint: String

        func validate() throws {
            let ids = workspaces.map(\.id)
            guard ids.count <= 256, Set(ids).count == ids.count,
                  Set(order) == Set(ids), order.count == ids.count,
                  Set(groups.map(\.id)).count == groups.count,
                  groups.count <= 256,
                  ([selectionFingerprint] + workspaces.map(\.metadataFingerprint) + groups.map(\.metadataFingerprint)).allSatisfy({
                      $0.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil
                  }) else { throw Failure.invalidInventory }
            for group in groups {
                guard Set(group.members).count == group.members.count,
                      Set(group.members).isSubset(of: Set(ids)),
                      group.anchorID == nil || group.members.contains(group.anchorID!) else {
                    throw Failure.invalidInventory
                }
                guard Set(workspaces.filter { $0.groupID == group.id }.map(\.id)) == Set(group.members) else {
                    throw Failure.invalidInventory
                }
            }
            guard workspaces.allSatisfy({ row in
                row.groupID == nil || groups.contains { $0.id == row.groupID }
            }) else { throw Failure.invalidInventory }
        }

        var fingerprint: String {
            get throws { try SidebarOrganizationPlan.fingerprint(self) }
        }
    }

    enum Evidence: String, Codable, Sendable {
        case registeredRepository, registeredProject, explicitReview
    }

    enum Destination: Codable, Equatable, Sendable {
        case existing(UUID)
        case create(key: String, name: String)
    }

    struct Assignment: Codable, Equatable, Sendable {
        let workspaceID: UUID
        let destination: Destination
        let evidence: Evidence
    }

    enum Failure: Error, Equatable {
        case invalidInventory, invalidPlan, staleInventory, manualMembershipProtected
        case unsafeDestination, externalIdentityAlreadyExists, mutationFailed
        case outcomeDiffers, rollbackConflict
    }

    let schemaVersion: Int
    let authority: String
    let id: UUID
    let sourceFingerprint: String
    let before: Inventory
    let assignments: [Assignment]

    init(id: UUID = UUID(), sourceFingerprint: String, before: Inventory,
         assignments: [Assignment]) throws {
        self.schemaVersion = 1
        self.authority = "none"
        self.id = id
        self.sourceFingerprint = sourceFingerprint
        self.before = before
        self.assignments = assignments
        try validate()
    }

    func externalID(for key: String) -> String {
        "ceo-organization:" + before.windowID.uuidString.lowercased() + ":" + key
    }

    func validate() throws {
        try before.validate()
        guard schemaVersion == 1, authority == "none",
              sourceFingerprint.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil,
              !assignments.isEmpty, assignments.count <= 256,
              Set(assignments.map(\.workspaceID)).count == assignments.count else {
            throw Failure.invalidPlan
        }
        var names: [String: String] = [:]
        for assignment in assignments {
            guard let workspace = before.workspaces.first(where: { $0.id == assignment.workspaceID }),
                  !workspace.generatedAnchor else { throw Failure.invalidPlan }
            // A legacy folder is manual by default. Even previous automatic
            // membership is left alone by this initial organization rail.
            guard workspace.groupID == nil else { throw Failure.manualMembershipProtected }
            switch assignment.destination {
            case .existing(let id):
                guard let group = before.groups.first(where: { $0.id == id }),
                      let anchor = group.anchorID, group.members.contains(anchor),
                      !group.generatedAnchor, !group.collapsed else { throw Failure.unsafeDestination }
            case .create(let key, let name):
                guard key.range(of: "^[a-z0-9][a-z0-9-]{0,63}$", options: .regularExpression) != nil,
                      !name.isEmpty, name.count <= 128,
                      name == name.trimmingCharacters(in: .whitespacesAndNewlines),
                      !name.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }),
                      names[key] == nil || names[key] == name else { throw Failure.invalidPlan }
                guard !before.groups.contains(where: { $0.externalID == externalID(for: key) }) else {
                    throw Failure.externalIdentityAlreadyExists
                }
                names[key] = name
            }
        }
    }

    static func fingerprint<T: Encodable>(_ value: T) throws -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return SHA256.hash(data: try encoder.encode(value)).map { String(format: "%02x", $0) }.joined()
    }
}
