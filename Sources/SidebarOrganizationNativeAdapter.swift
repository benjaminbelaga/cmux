import CmuxExtensionKit
import Foundation

/// Adapts the reviewed coordinator to existing native operations. The registry
/// fingerprint is freshly obtained by the issuing service immediately before
/// the synchronous main-actor transaction; it never comes from the plan.
@MainActor
final class SidebarOrganizationNativeAdapter: SidebarOrganizationPlanCoordinator.Adapter {
    typealias Plan = SidebarOrganizationPlan
    private let manager: TabManager
    private let registryFingerprint: String

    init(manager: TabManager, registryFingerprint: String) {
        self.manager = manager
        self.registryFingerprint = registryFingerprint
    }

    private struct WorkspaceMetadata: Encodable {
        let title: String
        let isPinned: Bool
        let importance: String
        let isMuted: Bool
        let color: String?
        let context: CmuxSidebarWorkspaceContext
        let sessions: [SidebarOrganizationInput.Session]
        let orderedSurfaceIDs: [UUID]
    }

    private struct GroupMetadata: Encodable {
        let name: String
        let color: String?
        let icon: String?
        let pinned: Bool
        let collapsed: Bool
        let externalID: String?
        let anchorID: UUID?
        let generatedAnchor: Bool
    }

    private struct Selection: Encodable {
        let focused: UUID?
        let selected: [UUID]
    }

    func sourceFingerprint() throws -> String {
        guard registryFingerprint.utf8.count == 64,
              registryFingerprint.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }) else {
            throw Plan.Failure.staleInventory
        }
        return registryFingerprint
    }

    func inventory() throws -> Plan.Inventory {
        guard let windowID = manager.windowId else { throw Plan.Failure.invalidInventory }
        let groups = Dictionary(manager.workspaceGroups.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        let membership = SidebarWorkspaceRenderItem.effectiveGroupIdByWorkspaceId(tabs: manager.tabs, groupsById: groups)
        let generated = Set(manager.workspaceGroups.filter(\.isGeneratedAnchor).compactMap(\.liveAnchorWorkspaceId))
        let nativeMetadata = SidebarOrganizationInventoryBuilder().make(tabManager: manager).metadata
        let workspaces = try manager.tabs.map { workspace in
            Plan.Workspace(id: workspace.id, revision: workspace.workspaceContext.context.revision,
                title: workspace.title,
                metadataFingerprint: try Plan.fingerprint(WorkspaceMetadata(title: workspace.title,
                    isPinned: workspace.isPinned, importance: workspace.importance.rawValue,
                    isMuted: workspace.isMuted, color: workspace.customColor, context: workspace.workspaceContext.context,
                    sessions: nativeMetadata.workspaces.first(where: { $0.id == workspace.id.uuidString })?.sessions ?? [],
                    orderedSurfaceIDs: workspace.sidebarOrderedPanelIds())),
                groupID: membership[workspace.id] ?? nil, generatedAnchor: generated.contains(workspace.id))
        }
        let groupRows = try manager.workspaceGroups.map { group in
            Plan.Group(id: group.id, name: group.name, externalID: group.externalID,
                anchorID: group.liveAnchorWorkspaceId, generatedAnchor: group.isGeneratedAnchor,
                pinned: group.isPinned, collapsed: group.isCollapsed,
                metadataFingerprint: try Plan.fingerprint(GroupMetadata(name: group.name,
                    color: group.customColor, icon: group.iconSymbol, pinned: group.isPinned,
                    collapsed: group.isCollapsed, externalID: group.externalID,
                    anchorID: group.liveAnchorWorkspaceId, generatedAnchor: group.isGeneratedAnchor)),
                members: manager.tabs.filter { membership[$0.id] == group.id }.map(\.id))
        }
        let result = Plan.Inventory(windowID: windowID, workspaces: workspaces, groups: groupRows,
            order: manager.tabs.map(\.id), selectionFingerprint: try Plan.fingerprint(Selection(
                focused: manager.selectedTabId,
                selected: manager.sidebarSelectedWorkspaceIds.sorted { $0.uuidString < $1.uuidString })))
        try result.validate()
        return result
    }

    func createGroup(name: String, children: [UUID], externalID: String) throws -> UUID {
        let before = try inventory()
        guard !children.isEmpty, Set(children).count == children.count,
              children.allSatisfy({ id in before.workspaces.contains { $0.id == id && $0.groupID == nil && !$0.generatedAnchor } }),
              !before.groups.contains(where: { $0.externalID == externalID }),
              let id = manager.createWorkspaceGroup(name: name, childWorkspaceIds: children,
                selectAnchor: false, collapseSidebarSelection: false, externalID: externalID) else {
            throw Plan.Failure.mutationFailed
        }
        return id
    }

    func addWorkspace(_ workspace: UUID, to group: UUID) throws {
        guard try inventory().workspaces.contains(where: { $0.id == workspace && $0.groupID == nil && !$0.generatedAnchor }),
              manager.workspaceGroups.contains(where: { $0.id == group }) else { throw Plan.Failure.mutationFailed }
        manager.addWorkspaceToGroup(workspaceId: workspace, groupId: group)
        guard try inventory().workspaces.first(where: { $0.id == workspace })?.groupID == group else { throw Plan.Failure.mutationFailed }
    }

    func removeWorkspace(_ workspace: UUID) throws {
        guard manager.tabs.contains(where: { $0.id == workspace }) else { throw Plan.Failure.mutationFailed }
        manager.removeWorkspaceFromGroup(workspaceId: workspace)
        guard try inventory().workspaces.first(where: { $0.id == workspace })?.groupID == nil else { throw Plan.Failure.mutationFailed }
    }

    func ungroup(_ group: UUID) throws {
        guard let current = manager.workspaceGroups.first(where: { $0.id == group }), !current.isGeneratedAnchor else {
            throw Plan.Failure.unsafeDestination
        }
        _ = manager.ungroupWorkspaceGroup(groupId: group, removeGeneratedAnchor: false)
        guard !manager.workspaceGroups.contains(where: { $0.id == group }) else { throw Plan.Failure.mutationFailed }
    }

    func restoreOrder(_ order: [UUID]) throws {
        switch manager.reorderWorkspaces(orderedWorkspaceIds: order) {
        case .success: break
        case .failure: throw Plan.Failure.mutationFailed
        }
        guard manager.tabs.map(\.id) == order else { throw Plan.Failure.mutationFailed }
    }
}
