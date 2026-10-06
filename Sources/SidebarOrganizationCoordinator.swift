@_spi(CmuxHostTransport) import CmuxExtensionKit
import Foundation
import Observation

/// Owns revision-guarded classification and bounded export receipts for one native window.
@MainActor
@Observable
final class SidebarOrganizationCoordinator {
    private let service: any SidebarOrganizationAnalyzing
    private let now: () -> Date
    private struct ExportReceipt {
        let input: SidebarOrganizationInput
        let nativeInventory: SidebarOrganizationInput
    }
    private var exports: [UUID: ExportReceipt] = [:]
    private let planIssuer: SidebarOrganizationPlanIssuer

    init(service: any SidebarOrganizationAnalyzing, registry: SidebarOrganizationRegistryReader? = nil,
         now: @escaping () -> Date = { Date() }) {
        self.service = service
        self.now = now
        planIssuer = SidebarOrganizationPlanIssuer(registry: registry)
    }

    func export(tabManager: TabManager, workspaceIDs: [UUID]? = nil,
                authorized: @MainActor () -> Bool = { true }) async throws -> Data {
        guard authorized(), !Task.isCancelled else { throw CancellationError() }
        let inventory = SidebarOrganizationInventoryBuilder(now: now).make(tabManager: tabManager, workspaceIDs: workspaceIDs)
        let input = try await service.prepare(inventory)
        try Task.checkCancellation()
        guard authorized() else { throw CancellationError() }
        let current = SidebarOrganizationInventoryBuilder(now: now).make(tabManager: tabManager, workspaceIDs: workspaceIDs)
        guard input.isValid, current.windowID == inventory.windowID,
              input.metadata.workspaces == inventory.metadata.workspaces,
              current.nativeComparison.workspaces == inventory.nativeComparison.workspaces,
              workspaceIDs == nil || Set(input.workspaces.compactMap { UUID(uuidString: $0.id) }) == Set(workspaceIDs!) else { throw SidebarOrganizationService.Failure.invalidInput }
        exports = exports.filter { now().timeIntervalSince($0.value.input.createdAt) <= 600 }
        if exports.count >= 8, let oldest = exports.min(by: { $0.value.input.createdAt < $1.value.input.createdAt })?.key { exports.removeValue(forKey: oldest) }
        let encoder = JSONEncoder(); encoder.dateEncodingStrategy = .iso8601
        let data = try encoder.encode(input)
        guard data.count <= 2 * 1024 * 1024 else { throw SidebarOrganizationService.Failure.invalidInput }
        exports[input.id] = ExportReceipt(input: input, nativeInventory: inventory)
        return data
    }

    func analyze(tabManager: TabManager, workspaceIDs: [UUID]? = nil,
                 exportID: UUID? = nil, review: Data? = nil,
                 authorized: @MainActor () -> Bool = { true }) async -> CmuxSidebarActionResult {
        await analyze(tabManager: tabManager, workspaceIDs: workspaceIDs,
            exportID: exportID, review: review, authorized: authorized, classificationID: nil)
    }

    private func analyze(tabManager: TabManager, workspaceIDs: [UUID]?,
                         exportID: UUID?, review: Data?, authorized: @MainActor () -> Bool,
                         classificationID: UUID?) async -> CmuxSidebarActionResult {
        guard authorized(), !Task.isCancelled else { return .cancelled }
        let input: SidebarOrganizationInput
        let nativeInventory: SidebarOrganizationInput
        if let exportID {
            guard let retained = exports[exportID], now().timeIntervalSince(retained.input.createdAt) <= 600 else { return stale }
            input = retained.input
            nativeInventory = retained.nativeInventory
        } else {
            input = SidebarOrganizationInventoryBuilder(now: now).make(tabManager: tabManager, workspaceIDs: workspaceIDs)
            nativeInventory = input
        }
        guard input.isValid, workspaceIDs == nil || Set(input.workspaces.compactMap { UUID(uuidString: $0.id) }) == Set(workspaceIDs!) else { return unavailable }
        do {
            let output = try await service.analyze(input, review: review)
            try Task.checkCancellation()
            let current = SidebarOrganizationInventoryBuilder(now: now).make(tabManager: tabManager, workspaceIDs: input.workspaces.compactMap { UUID(uuidString: $0.id) })
            guard authorized(), current.windowID == input.windowID,
                  current.nativeComparison.workspaces == nativeInventory.nativeComparison.workspaces else { return stale }
            // Validate the entire result before mutating native state. Only an
            // exact current repository attachment can automatically add its tag;
            // names, summaries, and semantic references remain reviewable.
            var prepared: [(Workspace, WorkspaceContextModel.Persisted)] = []
            for proposal in output.proposals {
                guard let id = UUID(uuidString: proposal.workspaceId), let workspace = tabManager.tabs.first(where: { $0.id == id }) else { return stale }
                let validation = WorkspaceContextModel()
                validation.restore(workspace.workspaceContext.persisted)
                try validation.storeProposal(expectedRevision: proposal.expectedRevision, proposal: proposal.contextProposal)
                let attached = Set((proposal.evidence ?? []).filter { evidence in
                    evidence.kind == "registered-repository-directory"
                        && input.workspaces.first(where: { $0.id == proposal.workspaceId })?.sessions.contains(where: { $0.sessionId == evidence.sessionId }) == true
                }.map(\.reference))
                let automatic = proposal.suggestedTags.filter {
                    $0.dimension == "repository" && $0.id.hasPrefix("repository:")
                        && attached.contains(String($0.id.dropFirst("repository:".count)))
                        && $0.source == "repo-classification:" + String($0.id.dropFirst("repository:".count))
                }.map(\.id)
                if !automatic.isEmpty {
                    let change = try validation.prepareProposal(expectedRevision: validation.context.revision,
                        proposalID: proposal.id, tagIDs: automatic, acceptTitle: false, acceptSummary: false)
                    validation.commitProposal(change, previousDisplayTitle: workspace.title, titleUndo: nil)
                }
                prepared.append((workspace, validation.persisted))
            }
            for (workspace, context) in prepared { workspace.workspaceContext.restore(context) }
            if let fingerprint = output.registryFingerprint,
               let inventory = try? SidebarOrganizationNativeAdapter(manager: tabManager,
                    registryFingerprint: fingerprint).inventory() {
                planIssuer.retain(output: output, input: input, inventory: inventory, classificationID: classificationID)
            }
            if let exportID { exports.removeValue(forKey: exportID) }
            return .accepted
        } catch is CancellationError { return .cancelled }
        catch { return unavailable }
    }

    /// Returns a reviewable native-issued plan. Applying accepts only this
    /// retained identity and performs an independent fresh registry readback.
    func preparePlan(tabManager: TabManager, workspaceIDs: [UUID]? = nil,
                     authorized: @MainActor () -> Bool) async throws -> SidebarOrganizationPlan {
        let classificationID = UUID()
        let result = await analyze(tabManager: tabManager, workspaceIDs: workspaceIDs,
            exportID: nil, review: nil, authorized: authorized, classificationID: classificationID)
        guard result.accepted else { throw SidebarOrganizationPlanIssuer.Failure.unavailable }
        return try await planIssuer.prepare(manager: tabManager, authorized: authorized,
            classificationID: classificationID)
    }

    func applyPlan(_ id: UUID, tabManager: TabManager,
                   authorized: @MainActor () -> Bool) async throws -> SidebarOrganizationPlanCoordinator.Receipt {
        try await planIssuer.apply(id, manager: tabManager, authorized: authorized)
    }

    func rollbackPlan(_ id: UUID, tabManager: TabManager,
                      authorized: @MainActor () -> Bool) throws {
        try planIssuer.rollback(id, manager: tabManager, authorized: authorized)
    }

    private var stale: CmuxSidebarActionResult {
        .rejected(String(localized: "sidebar.extensions.context.revisionConflict", defaultValue: "This workspace changed. Refresh before editing its context."), reason: .revisionConflict)
    }
    private var unavailable: CmuxSidebarActionResult {
        .rejected(String(localized: "sidebar.extensions.organization.unavailable", defaultValue: "Analysis is unavailable. Check the local classification engine and Python 3.11, then retry."))
    }
}
