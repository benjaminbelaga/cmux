import Foundation

/// Issues navigation plans only from a retained native classification result.
/// Callers receive identities; they cannot supply a plan, evidence or registry hash.
@MainActor
final class SidebarOrganizationPlanIssuer {
    typealias Plan = SidebarOrganizationPlan
    typealias Ledger = SidebarOrganizationPlacementLedger
    enum Failure: Error { case unavailable, stale, noCandidates, recoveryRequired }
    struct RecoveryRequired: Error {
        let placement: Ledger.RecoveryRequired
    }
    private struct Batch {
        let output: SidebarOrganizationOutput
        let input: SidebarOrganizationInput
        let inventory: Plan.Inventory
    }
    private struct Pending {
        let plan: Plan
        let placements: [Ledger.Snapshot]
    }
    private struct Applied {
        let core: SidebarOrganizationPlanCoordinator.Receipt
        let placement: Ledger.Receipt
    }
    private let registry: SidebarOrganizationRegistryReader?
    private let core = SidebarOrganizationPlanCoordinator()
    private let ledger = Ledger()
    private var batch: Batch?
    private var pending: [UUID: Pending] = [:]
    private var applied: [UUID: Applied] = [:]
    private var appliedOrder: [UUID] = []
    private var heldWorkspaces: Set<UUID> = []
    private var recoverySaturated = false

    init(registry: SidebarOrganizationRegistryReader?) { self.registry = registry }

    func retain(output: SidebarOrganizationOutput, input: SidebarOrganizationInput,
                inventory: Plan.Inventory) {
        guard registry != nil, let fingerprint = output.registryFingerprint,
              Self.canonical(fingerprint) else { batch = nil; return }
        batch = Batch(output: output, input: input.metadata, inventory: inventory)
    }

    func prepare(manager: TabManager, authorized: @MainActor () -> Bool) async throws -> Plan {
        guard !recoverySaturated, let registry, let retained = batch,
              let fingerprint = retained.output.registryFingerprint, authorized() else { throw Failure.unavailable }
        let adapter = SidebarOrganizationNativeAdapter(manager: manager, registryFingerprint: fingerprint)
        guard try adapter.inventory() == retained.inventory else { throw Failure.stale }
        let currentFingerprint = try await registry.read()
        try Task.checkCancellation()
        guard authorized(), currentFingerprint == fingerprint,
              try adapter.inventory() == retained.inventory else { throw Failure.stale }
        var assignments: [Plan.Assignment] = []
        for row in retained.output.proposals {
            guard let id = UUID(uuidString: row.workspaceId), !heldWorkspaces.contains(id),
                  let workspace = manager.tabs.first(where: { $0.id == id }),
                  workspace.workspaceContext.context.analyzedProposal?.id == row.id,
                  retained.inventory.workspaces.contains(where: { $0.id == id && $0.groupID == nil && !$0.generatedAnchor }),
                  let metadata = retained.input.workspaces.first(where: { $0.id == row.workspaceId }) else { continue }
            let context = workspace.workspaceContext.context
            guard context.analyzedProposal?.sourceFingerprint == row.sourceFingerprint,
                  !context.rejectedSourceFingerprints.contains(row.sourceFingerprint) else { continue }
            let manualDimensions = Set(context.tags.filter { $0.origin == .manual }.map(\.dimension))
            let rejectedIDs = Set(context.rejectedAutomaticTagIDs)
            let candidates = row.suggestedTags.filter {
                !manualDimensions.contains($0.dimension) && !rejectedIDs.contains($0.id)
            }
            // Exact registered project references and actual repository directories
            // are produced by the native-owned canonical runner, never by caller evidence.
            let projects = candidates.filter { tag in
                tag.origin == .automatic && tag.dimension == "project" && tag.id.hasPrefix("project:")
                    && tag.source == "projects-ceo:" + String(tag.id.dropFirst("project:".count))
                    && (row.evidence ?? []).contains { $0.kind == "registered-project-reference"
                        && $0.reference == String(tag.id.dropFirst("project:".count)) }
            }
            let repositories = candidates.filter { tag in
                tag.origin == .automatic && tag.dimension == "repository" && tag.id.hasPrefix("repository:")
                    && tag.source == "repo-classification:" + String(tag.id.dropFirst("repository:".count))
                    && (row.evidence ?? []).contains { evidence in
                        evidence.kind == "registered-repository-directory"
                            && evidence.reference == String(tag.id.dropFirst("repository:".count))
                            && metadata.sessions.contains { $0.sessionId == evidence.sessionId && $0.directory != nil }
                    }
            }
            let tags = projects.isEmpty ? repositories : projects
            guard tags.count == 1, let tag = tags.first else { continue }
            let key = (projects.isEmpty ? "repository-" : "project-") + String(try Plan.fingerprint(tag.id).prefix(24))
            let externalID = "ceo-organization:" + retained.inventory.windowID.uuidString.lowercased() + ":" + key
            let groups = retained.inventory.groups.filter { $0.externalID == externalID }
            guard groups.count <= 1 else { throw Failure.stale }
            let destination: Plan.Destination = groups.first.map { .existing($0.id) } ?? .create(key: key, name: tag.label)
            assignments.append(.init(workspaceID: id, destination: destination,
                evidence: projects.isEmpty ? .registeredRepository : .registeredProject))
        }
        guard !assignments.isEmpty else { throw Failure.noCandidates }
        let plan = try Plan(sourceFingerprint: currentFingerprint, before: retained.inventory, assignments: assignments)
        if pending.count >= 8 { pending.removeAll() }
        pending[plan.id] = Pending(plan: plan, placements: try snapshots(manager, plan.assignments.map(\.workspaceID)))
        return plan
    }

    func apply(_ id: UUID, manager: TabManager, authorized: @MainActor () -> Bool) async throws -> SidebarOrganizationPlanCoordinator.Receipt {
        guard !recoverySaturated, let registry, authorized() else { throw Failure.unavailable }
        if let retained = applied[id] {
            let ids = retained.core.plan.assignments.map(\.workspaceID)
            try verifyNotHeld(ids)
            let fresh = try await registry.read()
            try Task.checkCancellation()
            try verifyNotHeld(ids)
            let adapter = SidebarOrganizationNativeAdapter(manager: manager, registryFingerprint: fresh)
            guard authorized(), fresh == retained.core.plan.sourceFingerprint,
                  try adapter.inventory() == retained.core.after,
                  try snapshots(manager, retained.core.plan.assignments.map(\.workspaceID)) == retained.placement.after else { throw Failure.stale }
            return retained.core
        }
        guard let retained = pending[id] else { throw Failure.unavailable }
        let ids = retained.plan.assignments.map(\.workspaceID)
        try verifyNotHeld(ids)
        let fresh = try await registry.read()
        try Task.checkCancellation()
        try verifyNotHeld(ids)
        let adapter = SidebarOrganizationNativeAdapter(manager: manager, registryFingerprint: fresh)
        guard authorized(), fresh == retained.plan.sourceFingerprint,
              try adapter.inventory() == retained.plan.before,
              try snapshots(manager, retained.plan.assignments.map(\.workspaceID)) == retained.placements else { throw Failure.stale }
        let receipt: SidebarOrganizationPlanCoordinator.Receipt
        do { receipt = try core.apply(retained.plan, using: adapter) }
        catch let recovery as SidebarOrganizationPlanCoordinator.RecoveryRequired {
            hold(retained.plan.assignments.map(\.workspaceID))
            pending[id] = nil
            throw recovery
        }
        let expected = receipt.plan.assignments.map { assignment in
            Ledger.Snapshot(workspaceID: assignment.workspaceID,
                groupID: receipt.after.workspaces.first(where: { $0.id == assignment.workspaceID })?.groupID,
                placement: SidebarOrganizationPlacement(planID: receipt.plan.id))
        }
        do {
            guard try adapter.inventory() == receipt.after else { throw Failure.stale }
            for state in expected {
                guard let workspace = manager.tabs.first(where: { $0.id == state.workspaceID }),
                      workspace.groupId == state.groupID else { throw Failure.stale }
                workspace.groupPlacement = state.placement
            }
            let proof = try ledger.issue(planID: id, coreReceiptID: receipt.plan.id,
                before: retained.placements, expectedAfter: expected,
                observedAfter: snapshots(manager, retained.plan.assignments.map(\.workspaceID)))
            if applied.count >= 64, let oldest = appliedOrder.first {
                applied[oldest] = nil
                appliedOrder.removeFirst()
            }
            applied[id] = Applied(core: receipt, placement: proof)
            appliedOrder.append(id)
            pending[id] = nil
            return receipt
        } catch {
            pending[id] = nil
            throw recovery(id, phase: .applyReadback, cause: .outcomeDiffers,
                before: retained.placements, expected: expected, manager: manager)
        }
    }

    func rollback(_ id: UUID, manager: TabManager, authorized: @MainActor () -> Bool) throws {
        guard !recoverySaturated, let retained = applied[id], authorized() else { throw Failure.unavailable }
        let ids = retained.core.plan.assignments.map(\.workspaceID)
        try verifyNotHeld(ids)
        let restore = try ledger.beforeRollback(retained.placement, observed: snapshots(manager, ids))
        // There is no suspension between this provenance fence and core mutation.
        let adapter = SidebarOrganizationNativeAdapter(manager: manager, registryFingerprint: retained.core.plan.sourceFingerprint)
        do { try core.rollback(retained.core, using: adapter) }
        catch {
            // Preserve the core's exact partial/unavailable diagnostic when
            // compensation cannot establish a known outcome.
            _ = recovery(id, phase: .coreRollback, cause: .nativeFailure,
                before: retained.placement.before, expected: retained.placement.before, manager: manager)
            if let coreFailure = error as? SidebarOrganizationPlanCoordinator.RecoveryRequired { throw coreFailure }
            throw error
        }
        do {
            for state in restore {
                guard let workspace = manager.tabs.first(where: { $0.id == state.workspaceID }),
                      workspace.groupId == state.groupID else { throw Failure.stale }
                workspace.groupPlacement = state.placement
            }
            try ledger.finishRollback(retained.placement, observed: snapshots(manager, ids))
            applied[id] = nil
            appliedOrder.removeAll { $0 == id }
        } catch {
            throw recovery(id, phase: .placementRestore, cause: .outcomeDiffers,
                before: retained.placement.before, expected: retained.placement.before, manager: manager)
        }
    }

    private func recovery(_ id: UUID, phase: Ledger.RecoveryRequired.Phase,
                          cause: Ledger.RecoveryRequired.Cause,
                          before: [Ledger.Snapshot], expected: [Ledger.Snapshot],
                          manager: TabManager) -> RecoveryRequired {
        let observed = try? snapshots(manager, before.map(\.workspaceID))
        let actualCause: Ledger.RecoveryRequired.Cause = observed == nil ? .observedUnavailable : cause
        try? ledger.recordRecovery(planID: id, phase: phase, cause: actualCause,
            before: before, expected: expected, observed: observed)
        hold(before.map(\.workspaceID))
        return RecoveryRequired(placement: .init(planID: id, phase: phase, cause: actualCause,
            before: before, expected: expected, observed: observed))
    }

    private func hold(_ ids: [UUID]) {
        let combined = heldWorkspaces.union(ids)
        if combined.count > 256 { recoverySaturated = true }
        else { heldWorkspaces = combined }
    }

    private func verifyNotHeld(_ ids: [UUID]) throws {
        guard !recoverySaturated, heldWorkspaces.isDisjoint(with: ids) else { throw Failure.recoveryRequired }
    }

    private func snapshots(_ manager: TabManager, _ ids: [UUID]) throws -> [Ledger.Snapshot] {
        try ids.sorted { $0.uuidString < $1.uuidString }.map { id in
            guard let workspace = manager.tabs.first(where: { $0.id == id }) else { throw Failure.stale }
            return Ledger.Snapshot(workspaceID: id, groupID: workspace.groupId, placement: workspace.groupPlacement)
        }
    }

    private static func canonical(_ fingerprint: String) -> Bool {
        fingerprint.utf8.count == 64 && fingerprint.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }
}
