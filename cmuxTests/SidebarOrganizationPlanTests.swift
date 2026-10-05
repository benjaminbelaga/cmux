import Foundation
import Testing
@testable import cmux

@Suite
@MainActor
struct SidebarOrganizationPlanTests {
    typealias Plan = SidebarOrganizationPlan
    typealias Coordinator = SidebarOrganizationPlanCoordinator

    private final class NativeFixture: Coordinator.Adapter {
        var state: Plan.Inventory
        var source = String(repeating: "c", count: 64)
        var calls: [String] = []
        var failAddNumber: Int?
        var addNumber = 0
        var generatedInsteadOfChild = false
        var failAfterMutation = false
        var reorderCreatedChildren = false
        var restoreMode = "normal"

        init() {
            let ids = (0..<5).map { _ in UUID() }
            let groupID = UUID()
            state = .init(windowID: UUID(), workspaces: ids.enumerated().map { index, id in
                .init(id: id, revision: UInt64(index), title: "workspace-\(index)",
                      metadataFingerprint: String(repeating: "b", count: 64),
                      groupID: index == 0 ? groupID : nil, generatedAnchor: false)
            }, groups: [.init(id: groupID, name: "Manual", externalID: nil,
                              anchorID: ids[0], generatedAnchor: false, pinned: false,
                              collapsed: false, metadataFingerprint: String(repeating: "f", count: 64),
                              members: [ids[0]])], order: ids,
                          selectionFingerprint: String(repeating: "a", count: 64))
        }

        func inventory() throws -> Plan.Inventory { state }
        func sourceFingerprint() throws -> String { source }

        func createGroup(name: String, children: [UUID], externalID: String) throws -> UUID {
            calls.append("create")
            #expect(!children.isEmpty)
            let id = UUID()
            state.groups.append(.init(id: id, name: name, externalID: externalID,
                                      anchorID: children.first, generatedAnchor: generatedInsteadOfChild,
                                      pinned: false, collapsed: false,
                                      metadataFingerprint: String(repeating: "f", count: 64), members: children))
            for index in state.workspaces.indices where children.contains(state.workspaces[index].id) {
                state.workspaces[index].groupID = id
            }
            if reorderCreatedChildren { state.order.swapAt(1, 3) }
            if failAfterMutation { throw Plan.Failure.mutationFailed }
            return id
        }

        func addWorkspace(_ workspace: UUID, to group: UUID) throws {
            calls.append("add")
            addNumber += 1
            if addNumber == failAddNumber { throw Plan.Failure.mutationFailed }
            state.workspaces[state.workspaces.firstIndex { $0.id == workspace }!].groupID = group
            state.groups[state.groups.firstIndex { $0.id == group }!].members.append(workspace)
            if failAfterMutation { throw Plan.Failure.mutationFailed }
        }

        func removeWorkspace(_ workspace: UUID) throws {
            calls.append("remove")
            state.workspaces[state.workspaces.firstIndex { $0.id == workspace }!].groupID = nil
            for index in state.groups.indices { state.groups[index].members.removeAll { $0 == workspace } }
        }

        func ungroup(_ group: UUID) throws {
            calls.append("ungroup-preserve-children")
            for index in state.workspaces.indices where state.workspaces[index].groupID == group {
                state.workspaces[index].groupID = nil
            }
            state.groups.removeAll { $0.id == group }
        }

        func restoreOrder(_ order: [UUID]) throws {
            calls.append("restore-order")
            if restoreMode == "no-op" { return }
            state.order = order
            if restoreMode == "partial" { state.order.swapAt(1, 3) }
        }

        func createPlan(_ indices: [Int] = [1, 2]) throws -> Plan {
            try .init(sourceFingerprint: source, before: state, assignments: indices.map {
                .init(workspaceID: state.workspaces[$0].id,
                      destination: .create(key: "hr", name: "HR"), evidence: .registeredRepository)
            })
        }
    }

    @Test func groupsExistingChildrenWithoutChangingContextOrSelection() throws {
        let native = NativeFixture(), coordinator = Coordinator()
        let plan = try native.createPlan()
        let receipt = try coordinator.apply(plan, using: native)
        #expect(native.calls == ["create"])
        #expect(native.state.workspaces.count == plan.before.workspaces.count)
        #expect(receipt.createdGroups.count == 1)
        #expect(native.state.selectionFingerprint == plan.before.selectionFingerprint)
        #expect(native.state.groups.first == plan.before.groups.first)
        for row in native.state.workspaces {
            #expect(row.revision == plan.before.workspaces.first { $0.id == row.id }?.revision)
            #expect(row.metadataFingerprint == plan.before.workspaces.first { $0.id == row.id }?.metadataFingerprint)
        }
        try coordinator.rollback(receipt, using: native)
        #expect(native.state == plan.before)
        #expect(native.calls == ["create", "ungroup-preserve-children", "restore-order"])
    }

    @Test func exactReplayIsIdempotentButChangedRevisionIsHeld() throws {
        let native = NativeFixture(), coordinator = Coordinator()
        let plan = try native.createPlan(), receipt = try coordinator.apply(plan, using: native)
        #expect(try coordinator.apply(plan, using: native) == receipt)
        #expect(native.calls.count == 1)
        let original = native.state.workspaces[1]
        native.state.workspaces[1] = .init(id: original.id, revision: original.revision + 1,
                                         title: original.title, metadataFingerprint: original.metadataFingerprint,
                                         groupID: original.groupID, generatedAnchor: false)
        #expect(throws: Plan.Failure.staleInventory) { try coordinator.apply(plan, using: native) }
        #expect(throws: Plan.Failure.rollbackConflict) { try coordinator.rollback(receipt, using: native) }
        #expect(native.calls.count == 1)
    }

    @Test func metadataOnlyUnrelatedChangesSurviveScopedRollback() throws {
        let native = NativeFixture(), coordinator = Coordinator()
        let receipt = try coordinator.apply(native.createPlan(), using: native)
        let original = native.state.workspaces[4]
        native.state.workspaces[4] = .init(id: original.id, revision: 99, title: "Human title",
                                         metadataFingerprint: String(repeating: "d", count: 64),
                                         groupID: nil, generatedAnchor: false)
        let added = Plan.Workspace(id: UUID(), revision: 0, title: "New manual session",
                                   metadataFingerprint: String(repeating: "e", count: 64), groupID: nil, generatedAnchor: false)
        native.state.workspaces.append(added); native.state.order.append(added.id)
        let manual = native.state.groups[0]
        native.state.groups[0] = .init(id: manual.id, name: "Human folder rename", externalID: manual.externalID,
                                       anchorID: manual.anchorID, generatedAnchor: false, pinned: manual.pinned,
                                       collapsed: manual.collapsed, metadataFingerprint: String(repeating: "9", count: 64),
                                       members: manual.members)
        try coordinator.rollback(receipt, using: native)
        #expect(native.state.groups[0].name == "Human folder rename")
        #expect(native.state.workspaces[4].title == "Human title")
        #expect(native.state.workspaces.last == added)
        #expect(native.state.order.last == added.id)
    }

    @Test func manualMembershipAndGeneratedAnchorsCannotBeAutoMoved() throws {
        let native = NativeFixture()
        #expect(throws: Plan.Failure.manualMembershipProtected) { try native.createPlan([0]) }
        let row = native.state.workspaces[1]
        native.state.workspaces[1] = .init(id: row.id, revision: row.revision, title: row.title,
                                         metadataFingerprint: row.metadataFingerprint, groupID: nil, generatedAnchor: true)
        #expect(throws: Plan.Failure.invalidPlan) { try native.createPlan([1]) }
        #expect(native.calls.isEmpty)
    }

    @Test func emptyGeneratedOrCollapsedDestinationsAreHeld() throws {
        for variant in ["empty", "generated", "collapsed"] {
            let native = NativeFixture(), group = native.state.groups[0]
            if variant == "empty" { native.state.workspaces[0].groupID = nil }
            native.state.groups[0] = .init(id: group.id, name: group.name, externalID: nil,
                                          anchorID: variant == "empty" ? nil : group.anchorID,
                                          generatedAnchor: variant == "generated", pinned: false,
                                          collapsed: variant == "collapsed", metadataFingerprint: group.metadataFingerprint,
                                          members: variant == "empty" ? [] : group.members)
            #expect(throws: Plan.Failure.unsafeDestination) {
                try Plan(sourceFingerprint: native.source, before: native.state,
                         assignments: [.init(workspaceID: native.state.workspaces[1].id,
                                             destination: .existing(group.id), evidence: .explicitReview)])
            }
            #expect(native.calls.isEmpty)
        }
    }

    @Test func duplicateOrMismatchedTargetsAndExistingExternalIdentityAreHeld() throws {
        let native = NativeFixture()
        #expect(throws: Plan.Failure.invalidPlan) { try native.createPlan([1, 1]) }
        let plan = try native.createPlan()
        let original = native.state.groups[0]
        native.state.groups[0] = .init(id: original.id, name: original.name,
                                      externalID: plan.externalID(for: "hr"), anchorID: original.anchorID,
                                      generatedAnchor: false, pinned: false, collapsed: false,
                                      metadataFingerprint: original.metadataFingerprint, members: original.members)
        #expect(throws: Plan.Failure.externalIdentityAlreadyExists) { try native.createPlan() }
    }

    @Test func changedSourceWindowTopologyAndMetadataPreventEveryMutation() throws {
        for variant in ["source", "window", "order", "metadata"] {
            let native = NativeFixture(), coordinator = Coordinator()
            let plan = try native.createPlan()
            switch variant {
            case "source": native.source = String(repeating: "d", count: 64)
            case "window": native.state = .init(windowID: UUID(), workspaces: native.state.workspaces,
                                                groups: native.state.groups, order: native.state.order,
                                                selectionFingerprint: native.state.selectionFingerprint)
            case "order": native.state.order.swapAt(3, 4)
            default:
                let row = native.state.workspaces[3]
                native.state.workspaces[3] = .init(id: row.id, revision: row.revision, title: "New manual title",
                                                 metadataFingerprint: row.metadataFingerprint, groupID: nil, generatedAnchor: false)
            }
            #expect(throws: Plan.Failure.staleInventory) { try coordinator.apply(plan, using: native) }
            #expect(native.calls.isEmpty)
        }
    }

    @Test func partialFailureUndoesOnlyItsOwnMembership() throws {
        let native = NativeFixture(), coordinator = Coordinator(), before = native.state
        native.failAddNumber = 2
        let plan = try Plan(sourceFingerprint: native.source, before: before, assignments: [1, 2].map {
            .init(workspaceID: before.workspaces[$0].id, destination: .existing(before.groups[0].id), evidence: .explicitReview)
        })
        #expect(throws: Plan.Failure.mutationFailed) { try coordinator.apply(plan, using: native) }
        #expect(native.state == before)
        #expect(native.calls == ["add", "add", "remove", "restore-order"])
    }

    @Test func failureAfterNativeMutationReconcilesOnlyTheExactOwnedChange() throws {
        for variant in ["create", "add"] {
            let native = NativeFixture(), coordinator = Coordinator(), before = native.state
            native.failAfterMutation = true
            let plan: Plan
            if variant == "create" { plan = try native.createPlan() }
            else {
                plan = try Plan(sourceFingerprint: native.source, before: before,
                                assignments: [.init(workspaceID: before.workspaces[1].id,
                                                    destination: .existing(before.groups[0].id), evidence: .explicitReview)])
            }
            #expect(throws: Plan.Failure.mutationFailed) { try coordinator.apply(plan, using: native) }
            #expect(native.state == before)
            #expect(native.state.workspaces.count == before.workspaces.count)
        }
    }

    @Test func manuallyChangedMembershipAndPlacementBlockRollback() throws {
        for variant in ["child", "placement"] {
            let native = NativeFixture(), coordinator = Coordinator()
            let receipt = try coordinator.apply(native.createPlan(), using: native)
            if variant == "child" {
                let group = try #require(receipt.createdGroups["hr"])
                try native.addWorkspace(native.state.workspaces[4].id, to: group)
            } else { native.state.order.swapAt(0, 1) }
            let count = native.calls.count
            #expect(throws: Plan.Failure.rollbackConflict) { try coordinator.rollback(receipt, using: native) }
            #expect(native.calls.count == count)
        }
    }

    @Test func forgedReceiptUnknownCreatedKeyFailsWithoutMutation() throws {
        let native = NativeFixture(), coordinator = Coordinator()
        let receipt = try coordinator.apply(native.createPlan(), using: native)
        let forged = Coordinator.Receipt(plan: receipt.plan, after: receipt.after, createdGroups: ["unknown": UUID()])
        #expect(throws: Plan.Failure.outcomeDiffers) { try coordinator.rollback(forged, using: native) }
        #expect(native.calls == ["create"])
    }

    @Test func rollbackCannotReportSuccessWhenNativeOrderRestorationDidNotApply() throws {
        for mode in ["no-op", "partial"] {
            let native = NativeFixture(), coordinator = Coordinator()
            native.reorderCreatedChildren = true
            let receipt = try coordinator.apply(native.createPlan(), using: native)
            #expect(native.state.order != receipt.plan.before.order)
            native.restoreMode = mode
            #expect(throws: Plan.Failure.rollbackConflict) { try coordinator.rollback(receipt, using: native) }
        }
    }

    @Test func aValidButUnissuedReceiptCannotAuthorizeRollback() throws {
        let native = NativeFixture(), coordinator = Coordinator()
        let receipt = try coordinator.apply(native.createPlan(), using: native)
        let unissuedPlan = try Plan(sourceFingerprint: receipt.plan.sourceFingerprint,
                                    before: receipt.plan.before, assignments: receipt.plan.assignments)
        let forged = Coordinator.Receipt(plan: unissuedPlan, after: receipt.after, createdGroups: receipt.createdGroups)
        #expect(throws: Plan.Failure.rollbackConflict) { try coordinator.rollback(forged, using: native) }
        #expect(native.calls == ["create"])
    }

    @Test func invalidAuthorityDecodedFromExternalPlanCannotMutate() throws {
        let native = NativeFixture(), coordinator = Coordinator()
        let plan = try native.createPlan()
        let encoded = try JSONEncoder().encode(plan)
        let text = String(decoding: encoded, as: UTF8.self).replacingOccurrences(of: "\"none\"", with: "\"admin\"")
        let forged = try JSONDecoder().decode(Plan.self, from: Data(text.utf8))
        #expect(throws: Plan.Failure.invalidPlan) { try coordinator.apply(forged, using: native) }
        #expect(native.calls.isEmpty)
    }
}
