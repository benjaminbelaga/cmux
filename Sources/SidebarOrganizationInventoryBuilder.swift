import CmuxExtensionKit
import Foundation

/// Collects every native workspace independently of rendering, search, and group collapse.
@MainActor
struct SidebarOrganizationInventoryBuilder {
    var projector = SidebarExtensionRuntimeProjector()
    var now: () -> Date = { Date() }

    func make(tabManager: TabManager, workspaceIDs: [UUID]? = nil) -> SidebarOrganizationInput {
        let groups = Dictionary(tabManager.workspaceGroups.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        let membership = SidebarWorkspaceRenderItem.effectiveGroupIdByWorkspaceId(tabs: tabManager.tabs, groupsById: groups)
        let selected = workspaceIDs.map(Set.init)
        let observedAt = now()
        var seen = Set<UUID>()
        let workspaces = tabManager.tabs.filter { (selected == nil || selected!.contains($0.id)) && seen.insert($0.id).inserted }.map { workspace in
            let context = workspace.workspaceContext.context
            var identities = Set<String>()
            let sessions = workspace.sidebarOrderedPanelIds().flatMap { panelID in
                (projector.observations(workspace: workspace, panelID: panelID) ?? []).compactMap { observation -> SidebarOrganizationInput.Session? in
                    guard let sid = observation.sessionID, !sid.isEmpty,
                          let tool = observation.toolID, let generation = observation.processGeneration,
                          identities.insert(tool + ":" + sid + ":" + String(generation)).inserted else { return nil }
                    return .init(toolId: tool, sessionId: sid,
                        directory: workspace.reportedPanelDirectory(panelId: panelID),
                        title: workspace.panelTitle(panelId: panelID) ?? "", context: nil,
                        surfaceId: panelID.uuidString, processGeneration: generation)
                }
            }
            let groupID = membership[workspace.id] ?? nil
            let references = context.sourceReferences.map { values in
                Array(values.filter(\.isStructurallyValid).prefix(32).map { reference in
                    SidebarOrganizationInput.SourceReference(provider: reference.provider,
                        accountRef: reference.accountRef, directoryUserId: reference.directoryUserId,
                        resourceId: reference.resourceId, messageId: reference.messageId,
                        evidenceFingerprint: reference.evidenceFingerprint)
                })
            }
            let observations = Array(workspace.sidebarOrderedPanelIds().compactMap { panelID in
                CmuxSidebarServiceObservation.browser(url: workspace.browserPanel(for: panelID)?.currentURL,
                    surfaceId: panelID, observedAt: observedAt)
            }.prefix(32).map { observation in
                SidebarOrganizationInput.ServiceObservation(service: observation.service,
                    surfaceId: observation.surfaceId.uuidString, kind: observation.kind.rawValue,
                    observedAt: observation.observedAt)
            })
            return SidebarOrganizationInput.Workspace(id: workspace.id.uuidString, title: workspace.title,
                revision: context.revision, groupId: groupID?.uuidString,
                tags: context.tags, aliases: context.aliases, summary: context.summary,
                rejectedAutomaticTagIDs: context.rejectedAutomaticTagIDs,
                rejectedSourceFingerprints: context.rejectedSourceFingerprints, sessions: sessions,
                groupName: groupID.flatMap { groups[$0]?.name },
                // Legacy native groups have no stored classification origin.
                // They retain the conservative manual boundary.
                groupOrigin: groupID == nil ? nil : "manual",
                sourceReferences: references, serviceObservations: observations.isEmpty ? nil : observations)
        }
        return .init(id: UUID(), windowID: tabManager.windowId, createdAt: observedAt, workspaces: workspaces)
    }
}
