import Foundation

/// Sequences existing native group operations without creating or closing a workspace.
@MainActor
final class SidebarOrganizationPlanCoordinator {
    typealias Plan = SidebarOrganizationPlan

    @MainActor
    protocol Adapter: AnyObject {
        func inventory() throws -> Plan.Inventory
        // The host supplies the current registered source fingerprint; never
        // accept the plan's own value as a substitute for this readback.
        func sourceFingerprint() throws -> String
        // Implementations use TabManager's existing operations. Creation must
        // pass existing children, selectAnchor:false and collapseSidebarSelection:false.
        func createGroup(name: String, children: [UUID], externalID: String) throws -> UUID
        func addWorkspace(_ workspace: UUID, to group: UUID) throws
        func removeWorkspace(_ workspace: UUID) throws
        // Only ungroup(removeGeneratedAnchor:false), never deleteWorkspaceGroup.
        func ungroup(_ group: UUID) throws
        func restoreOrder(_ order: [UUID]) throws
    }

    struct Receipt: Codable, Equatable, Sendable {
        let plan: Plan
        let after: Plan.Inventory
        let createdGroups: [String: UUID]
    }

    private var receipts: [UUID: Receipt] = [:]
    private var receiptOrder: [UUID] = []

    func apply(_ plan: Plan, using adapter: any Adapter) throws -> Receipt {
        try plan.validate()
        guard try adapter.sourceFingerprint() == plan.sourceFingerprint else { throw Plan.Failure.staleInventory }
        let before = try adapter.inventory()
        try before.validate()
        if let receipt = receipts[plan.id], receipt.plan == plan, before == receipt.after { return receipt }
        guard before == plan.before else { throw Plan.Failure.staleInventory }
        var created: [String: UUID] = [:]
        var changed: [UUID] = []
        do {
            for assignment in plan.assignments {
                let destination: UUID
                switch assignment.destination {
                case .existing(let id): destination = id
                case .create(let key, let name):
                    if let id = created[key] { destination = id }
                    else {
                        let children = plan.assignments.filter { row in
                            if case .create(let other, _) = row.destination { return other == key }
                            return false
                        }.map(\.workspaceID)
                        destination = try adapter.createGroup(name: name, children: children,
                                                              externalID: plan.externalID(for: key))
                        guard !before.groups.contains(where: { $0.id == destination }),
                              !created.values.contains(destination) else { throw Plan.Failure.outcomeDiffers }
                        created[key] = destination
                        changed.append(contentsOf: children)
                    }
                }
                // Newly created groups already contain all their children.
                if case .existing = assignment.destination {
                    try adapter.addWorkspace(assignment.workspaceID, to: destination)
                    changed.append(assignment.workspaceID)
                }
            }
            let after = try adapter.inventory()
            try validateOutcome(plan, after: after, created: created)
            let receipt = Receipt(plan: plan, after: after, createdGroups: created)
            if receipts.count >= 64, let oldest = receiptOrder.first {
                receipts[oldest] = nil
                receiptOrder.removeFirst()
            }
            receipts[plan.id] = receipt
            receiptOrder.append(plan.id)
            return receipt
        } catch {
            // A partial native result is inspected before undo. Unknown changes
            // fail closed; no generic reset can overwrite another operation.
            let current = try adapter.inventory()
            let partial = Receipt(plan: plan, after: current, createdGroups: created)
            try undo(partial, changed: Set(changed), using: adapter)
            throw error
        }
    }

    func rollback(_ receipt: Receipt, using adapter: any Adapter) throws {
        try receipt.plan.validate()
        try validateOutcome(receipt.plan, after: receipt.after, created: receipt.createdGroups)
        let current = try adapter.inventory()
        try current.validate()
        guard scoped(current, receipt: receipt) == scoped(receipt.after, receipt: receipt) else {
            throw Plan.Failure.rollbackConflict
        }
        try undo(receipt, changed: Set(receipt.plan.assignments.map(\.workspaceID)), using: adapter)
        receipts[receipt.plan.id] = nil
        receiptOrder.removeAll { $0 == receipt.plan.id }
    }

    private func validateOutcome(_ plan: Plan, after: Plan.Inventory, created: [String: UUID]) throws {
        try after.validate()
        let expectedKeys = Set(plan.assignments.compactMap { row -> String? in
            if case .create(let key, _) = row.destination { return key }; return nil
        })
        guard Set(created.keys) == expectedKeys, Set(created.values).count == created.count else {
            throw Plan.Failure.outcomeDiffers
        }
        guard after.windowID == plan.before.windowID,
              after.selectionFingerprint == plan.before.selectionFingerprint,
              Set(after.workspaces.map(\.id)) == Set(plan.before.workspaces.map(\.id)),
              Set(after.groups.map(\.id)) == Set(plan.before.groups.map(\.id)).union(created.values) else {
            throw Plan.Failure.outcomeDiffers
        }
        for before in plan.before.workspaces {
            guard var actual = after.workspaces.first(where: { $0.id == before.id }) else { throw Plan.Failure.outcomeDiffers }
            let assignment = plan.assignments.first { $0.workspaceID == before.id }
            let destination: UUID?
            switch assignment?.destination {
            case .existing(let id): destination = id
            case .create(let key, _): destination = created[key]
            case nil: destination = before.groupID
            }
            guard actual.groupID == destination else { throw Plan.Failure.outcomeDiffers }
            actual.groupID = before.groupID
            guard actual == before else { throw Plan.Failure.outcomeDiffers }
        }
        for before in plan.before.groups {
            guard var actual = after.groups.first(where: { $0.id == before.id }) else { throw Plan.Failure.outcomeDiffers }
            let additions = plan.assignments.filter { $0.destination == .existing(before.id) }.map(\.workspaceID)
            guard Set(actual.members) == Set(before.members).union(additions) else { throw Plan.Failure.outcomeDiffers }
            actual.members = before.members
            guard actual == before else { throw Plan.Failure.outcomeDiffers }
        }
        for (key, id) in created {
            let assignments = plan.assignments.filter { if case .create(let k, _) = $0.destination { return k == key }; return false }
            guard let first = assignments.first, case .create(_, let name) = first.destination,
                  let group = after.groups.first(where: { $0.id == id }),
                  group.name == name, group.externalID == plan.externalID(for: key),
                  group.anchorID == assignments.first?.workspaceID,
                  !group.generatedAnchor, !group.pinned, !group.collapsed,
                  Set(group.members) == Set(assignments.map(\.workspaceID)) else { throw Plan.Failure.outcomeDiffers }
        }
        let affected = Set(plan.assignments.map(\.workspaceID))
        guard after.order.filter({ !affected.contains($0) }) == plan.before.order.filter({ !affected.contains($0) }) else {
            throw Plan.Failure.outcomeDiffers
        }
    }

    private struct Scope: Equatable {
        let windowID: UUID
        let workspaces: [Plan.Workspace]
        let groups: [Plan.Group]
        let order: [UUID]
    }

    private func scoped(_ inventory: Plan.Inventory, receipt: Receipt) -> Scope {
        let workspaces = Set(receipt.plan.assignments.map(\.workspaceID))
        let groups = Set(receipt.createdGroups.values).union(receipt.plan.assignments.compactMap { row in
            if case .existing(let id) = row.destination { return id }; return nil
        })
        var fence = workspaces
        for (index, id) in receipt.after.order.enumerated() where workspaces.contains(id) {
            if index > 0 { fence.insert(receipt.after.order[index - 1]) }
            if index + 1 < receipt.after.order.count { fence.insert(receipt.after.order[index + 1]) }
        }
        return Scope(windowID: inventory.windowID,
                     workspaces: inventory.workspaces.filter { workspaces.contains($0.id) },
                     groups: inventory.groups.filter { groups.contains($0.id) },
                     order: inventory.order.filter { fence.contains($0) })
    }

    private func undo(_ receipt: Receipt, changed: Set<UUID>, using adapter: any Adapter) throws {
        let current = try adapter.inventory()
        try current.validate()
        guard current.windowID == receipt.plan.before.windowID,
              Set(current.workspaces.map(\.id)).isSuperset(of: Set(receipt.plan.before.workspaces.map(\.id))) else {
            throw Plan.Failure.rollbackConflict
        }
        for id in changed {
            guard var row = current.workspaces.first(where: { $0.id == id }),
                  let original = receipt.plan.before.workspaces.first(where: { $0.id == id }) else {
                throw Plan.Failure.rollbackConflict
            }
            row.groupID = original.groupID
            guard row == original else { throw Plan.Failure.rollbackConflict }
        }
        // Refuse a created group that gained an unowned child or changed its
        // stable identity. Ungrouping it would overwrite manual membership.
        for (key, id) in receipt.createdGroups {
            let planned = receipt.plan.assignments.filter { if case .create(let k, _) = $0.destination { return k == key }; return false }
            guard let group = current.groups.first(where: { $0.id == id }),
                  case .create(_, let name) = planned.first?.destination,
                  group.name == name, group.anchorID == planned.first?.workspaceID,
                  group.externalID == receipt.plan.externalID(for: key),
                  Set(group.members).isSubset(of: changed), !group.generatedAnchor,
                  !group.pinned, !group.collapsed else {
                throw Plan.Failure.rollbackConflict
            }
        }
        for id in receipt.createdGroups.values { try adapter.ungroup(id) }
        for id in changed where !receipt.createdGroups.values.contains(where: { group in
            current.workspaces.contains { $0.id == id && $0.groupID == group }
        }) {
            let workspace = current.workspaces.first { $0.id == id }
            guard workspace?.groupID == nil || receipt.plan.assignments.contains(where: {
                $0.workspaceID == id && $0.destination == .existing(workspace!.groupID!)
            }) else { throw Plan.Failure.rollbackConflict }
            if workspace?.groupID != nil { try adapter.removeWorkspace(id) }
        }
        var order = try adapter.inventory().order.filter { !changed.contains($0) }
        // Insert only our members relative to their original neighbours.
        // Unrelated additions/reordering retain their current relative order.
        for id in receipt.plan.before.order where changed.contains(id) {
            let index = receipt.plan.before.order.firstIndex(of: id)!
            let successors = receipt.plan.before.order.dropFirst(index + 1)
            if let next = successors.first(where: { order.contains($0) }), let slot = order.firstIndex(of: next) {
                order.insert(id, at: slot)
            } else { order.append(id) }
        }
        try adapter.restoreOrder(order)
        let after = try adapter.inventory()
        let oldGroups = current.groups.filter { !receipt.createdGroups.values.contains($0.id) }.map { group -> Plan.Group in
            var copy = group
            copy.members.removeAll { changed.contains($0) }
            return copy
        }
        guard changed.allSatisfy({ id in
            after.workspaces.first(where: { $0.id == id }) == receipt.plan.before.workspaces.first(where: { $0.id == id })
        }), after.groups == oldGroups,
        after.selectionFingerprint == current.selectionFingerprint,
        Set(after.workspaces.map(\.id)) == Set(current.workspaces.map(\.id)),
        !after.groups.contains(where: { receipt.createdGroups.values.contains($0.id) }) else {
            throw Plan.Failure.rollbackConflict
        }
    }
}
