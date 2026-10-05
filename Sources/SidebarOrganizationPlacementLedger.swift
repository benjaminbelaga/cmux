import Foundation

/// Outer native provenance fence. Core topology receipts remain a separate authority.
/// The native issuer calls this only with an actual core receipt and native readback.
@MainActor
final class SidebarOrganizationPlacementLedger {
    struct Snapshot: Codable, Equatable, Sendable {
        let workspaceID: UUID
        let groupID: UUID?
        let placement: SidebarOrganizationPlacement?
    }

    struct Receipt: Equatable, Sendable {
        fileprivate let issuanceID: UUID
        let planID: UUID
        let coreReceiptID: UUID
        let before: [Snapshot]
        let after: [Snapshot]
    }

    enum Failure: Error { case invalidSnapshot, invalidReceipt, outcomeDiffers, stalePlacement, recoveryRequired }

    /// Diagnostic only; no receipt, retry grant, or replacement inventory is issued.
    struct RecoveryRequired: Equatable, Sendable {
        enum Phase: String, Sendable { case applyReadback, coreRollback, placementRestore }
        enum Cause: String, Sendable { case nativeFailure, observedUnavailable, outcomeDiffers }
        let planID: UUID
        let phase: Phase
        let cause: Cause
        let before: [Snapshot]
        let expected: [Snapshot]
        let observed: [Snapshot]?
    }

    private var receipts: [UUID: Receipt] = [:]
    private var order: [UUID] = []
    private var held: Set<UUID> = []
    private var rollbackStarted: Set<UUID> = []
    private(set) var recoveries: [RecoveryRequired] = []

    private func canonical(_ values: [Snapshot]) throws -> [Snapshot] {
        guard !values.isEmpty, values.count <= 256,
              Set(values.map(\.workspaceID)).count == values.count else { throw Failure.invalidSnapshot }
        return values.sorted { $0.workspaceID.uuidString < $1.workspaceID.uuidString }
    }

    func issue(planID: UUID, coreReceiptID: UUID, before: [Snapshot],
               expectedAfter: [Snapshot], observedAfter: [Snapshot]) throws -> Receipt {
        guard planID == coreReceiptID, receipts[planID] == nil, !held.contains(planID) else {
            throw Failure.invalidReceipt
        }
        let before = try canonical(before), after = try canonical(expectedAfter)
        guard before.map(\.workspaceID) == after.map(\.workspaceID),
              after.allSatisfy({ $0.groupID != nil && $0.placement?.origin == .automatic
                  && $0.placement?.planID == planID }) else { throw Failure.invalidSnapshot }
        guard try canonical(observedAfter) == after else { throw Failure.outcomeDiffers }
        let receipt = Receipt(issuanceID: UUID(), planID: planID, coreReceiptID: coreReceiptID,
                              before: before, after: after)
        if order.count >= 64, let oldest = order.first {
            order.removeFirst()
            receipts[oldest] = nil
            held.remove(oldest)
            rollbackStarted.remove(oldest)
        }
        receipts[planID] = receipt
        order.append(planID)
        return receipt
    }

    private func retained(_ receipt: Receipt) throws {
        guard receipts[receipt.planID] == receipt else { throw Failure.invalidReceipt }
        guard !held.contains(receipt.planID) else { throw Failure.recoveryRequired }
    }

    /// Call immediately before the synchronous core rollback, with no await in between.
    /// Nil legacy provenance is compared exactly: a manual same-group choice is drift.
    func beforeRollback(_ receipt: Receipt, observed: [Snapshot]) throws -> [Snapshot] {
        try retained(receipt)
        guard try canonical(observed) == receipt.after else { throw Failure.stalePlacement }
        rollbackStarted.insert(receipt.planID)
        return receipt.before
    }

    /// Core rollback must succeed first; then restore only these affected placements.
    /// Consume the receipt only after exact native restoration readback succeeds.
    func finishRollback(_ receipt: Receipt, observed: [Snapshot]) throws {
        try retained(receipt)
        guard rollbackStarted.contains(receipt.planID) else { throw Failure.invalidReceipt }
        guard try canonical(observed) == receipt.before else { throw Failure.outcomeDiffers }
        receipts[receipt.planID] = nil
        order.removeAll { $0 == receipt.planID }
        rollbackStarted.remove(receipt.planID)
    }

    /// Unknown native state is retained for human diagnosis and blocks automatic retry.
    func recordRecovery(planID: UUID, phase: RecoveryRequired.Phase,
                        cause: RecoveryRequired.Cause, before: [Snapshot],
                        expected: [Snapshot], observed: [Snapshot]?) throws {
        let before = try canonical(before), expected = try canonical(expected)
        let observed = try observed.map(canonical)
        guard before.map(\.workspaceID) == expected.map(\.workspaceID) else { throw Failure.invalidSnapshot }
        if let receipt = receipts[planID] {
            guard receipt.before == before,
                  expected == receipt.before || expected == receipt.after else { throw Failure.invalidReceipt }
        }
        held.insert(planID)
        rollbackStarted.remove(planID)
        recoveries.append(.init(planID: planID, phase: phase, cause: cause,
                                before: before, expected: expected, observed: observed))
        if recoveries.count > 32 { recoveries.removeFirst(recoveries.count - 32) }
        // Unissued failures are diagnostic holds, never indefinitely growing state.
        if held.count > 96 {
            let retainedPlans = Set(order).union(recoveries.map(\.planID))
            held.formIntersection(retainedPlans)
        }
    }
}
