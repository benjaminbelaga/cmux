@_spi(CmuxHostTransport) import CmuxExtensionKit
import CmuxControlSocket
import Foundation

extension TerminalController {
    /// Local-only classification bridge. No command or arbitrary filesystem path is accepted.
    @MainActor
    func workspaceOrganizationResponse(_ request: ControlRequest) async -> String {
        let params = request.params.mapValues(\.foundationObject)
        let id = request.id?.foundationObject
        guard let socketAuthorization = SocketCommandAuthorization.current, socketAuthorization.isValid else {
            return v2Error(id: id, code: "access_denied", message: "Socket authorization is no longer current")
        }
        guard let manager = v2ResolveTabManager(params: params) else {
            return v2Error(id: id, code: "not_found", message: String(localized: "sidebar.extensions.context.invalidPayload", defaultValue: "The context request is invalid or exceeds its limits."))
        }
        if ["workspace.organization.plan", "workspace.organization.apply", "workspace.organization.rollback"].contains(request.method) {
            return await workspaceOrganizationPlanResponse(request, manager: manager)
        }
        if request.method == "workspace.source.attach" {
            let keys = Set(params.keys)
            guard keys == Set(["window_id", "workspace_id", "expected_revision", "request_id", "source_reference"]),
                  let rawWindow = params["window_id"] as? String, let windowID = UUID(uuidString: rawWindow),
                  manager.windowId == windowID,
                  AppDelegate.shared?.tabManagerFor(windowId: windowID) === manager,
                  let data = try? JSONSerialization.data(withJSONObject: params), data.count <= 8192,
                  let packet = try? JSONDecoder().decode(SidebarSourceReferenceResolver.Request.self, from: data) else {
                return v2Error(id: id, code: "invalid_params", message: String(localized: "sidebar.extensions.context.invalidPayload", defaultValue: "The context request is invalid or exceeds its limits."))
            }
            do {
                let revision = try await manager.sidebarSourceReferenceCoordinator.attach(
                    manager: manager, workspaceID: packet.workspaceID, expectedRevision: packet.expectedRevision,
                    requestID: packet.requestID, reference: packet.sourceReference, authorized: {
                        socketAuthorization.isValid && manager.windowId == windowID
                            && AppDelegate.shared?.tabManagerFor(windowId: windowID) === manager
                            && AppDelegate.shared?.tabManagerFor(tabId: packet.workspaceID) === manager
                            && self.v2ResolveTabManager(params: params) === manager
                    })
                return v2Ok(id: id, result: ["attached": true, "workspace_id": packet.workspaceID.uuidString,
                    "revision": revision, "source_reference_fingerprint": packet.sourceReference.fingerprint ?? ""])
            } catch is CancellationError {
                return v2Error(id: id, code: "cancelled", message: String(localized: "sidebar.extensions.action.unavailable", defaultValue: "Action is unavailable"))
            } catch SidebarSourceReferenceCoordinator.Failure.revisionConflict {
                return v2Error(id: id, code: "revision_conflict", message: String(localized: "sidebar.extensions.context.revisionConflict", defaultValue: "This workspace changed. Refresh before editing its context."))
            } catch {
                return v2Error(id: id, code: "source_reference_hold", message: String(localized: "sidebar.extensions.action.urlRejected", defaultValue: "URL could not be opened"))
            }
        }
        let workspaceIDs: [UUID]?
        if let value = params["workspace_id"] {
            guard params["workspace_ids"] == nil, let text = value as? String,
                  let uuid = UUID(uuidString: text), manager.tabs.contains(where: { $0.id == uuid }) else {
                return v2Error(id: id, code: "invalid_params", message: String(localized: "sidebar.extensions.context.invalidPayload", defaultValue: "The context request is invalid or exceeds its limits."))
            }
            workspaceIDs = [uuid]
        } else if let values = params["workspace_ids"] {
            guard let strings = values as? [String], !strings.isEmpty, strings.count <= 256 else {
                return v2Error(id: id, code: "invalid_params", message: String(localized: "sidebar.extensions.context.invalidPayload", defaultValue: "The context request is invalid or exceeds its limits."))
            }
            let ids = strings.compactMap(UUID.init(uuidString:))
            guard ids.count == strings.count, Set(ids).count == ids.count else {
                return v2Error(id: id, code: "invalid_params", message: String(localized: "sidebar.extensions.context.invalidPayload", defaultValue: "The context request is invalid or exceeds its limits."))
            }
            workspaceIDs = ids
        } else { workspaceIDs = nil }
        if request.method == "workspace.context.export" {
            do {
                let data = try await manager.sidebarOrganizationCoordinator.export(tabManager: manager, workspaceIDs: workspaceIDs,
                    authorized: {
                        socketAuthorization.isValid && self.v2ResolveTabManager(params: params) === manager
                            && manager.windowId.flatMap { AppDelegate.shared?.tabManagerFor(windowId: $0) } === manager
                    })
                let result = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
                return v2Ok(id: id, result: result)
            } catch { return v2Error(id: id, code: "classification_unavailable", message: String(localized: "sidebar.extensions.organization.unavailable", defaultValue: "Analysis is unavailable. Check the local classification engine and Python 3.11, then retry.")) }
        }
        guard let rawID = params["export_id"] as? String, let exportID = UUID(uuidString: rawID),
              let review = params["review"] as? [String: Any],
              let data = try? JSONSerialization.data(withJSONObject: review), data.count <= 2 * 1024 * 1024 else {
            return v2Error(id: id, code: "invalid_params", message: String(localized: "sidebar.extensions.context.invalidPayload", defaultValue: "The context request is invalid or exceeds its limits."))
        }
        guard let windowID = manager.windowId else { return v2Error(id: id, code: "not_found", message: "Window is unavailable") }
        let result = await manager.sidebarOrganizationCoordinator.analyze(tabManager: manager,
            workspaceIDs: workspaceIDs, exportID: exportID, review: data, authorized: {
                socketAuthorization.isValid && manager.windowId == windowID
                    && AppDelegate.shared?.tabManagerFor(windowId: windowID) === manager
                    && self.v2ResolveTabManager(params: params) === manager
            })
        return result.accepted ? v2Ok(id: id, result: ["accepted": true, "proposals_retained": true])
            : v2Error(id: id, code: result.rejectionReason?.rawValue ?? "rejected", message: result.message ?? String(localized: "sidebar.extensions.context.invalidPayload", defaultValue: "The context request is invalid or exceeds its limits."))
    }

    @MainActor
    private func workspaceOrganizationPlanResponse(_ request: ControlRequest, manager: TabManager) async -> String {
        let params = request.params.mapValues(\.foundationObject)
        let id = request.id?.foundationObject
        guard let socketAuthorization = SocketCommandAuthorization.current, socketAuthorization.isValid else {
            return v2Error(id: id, code: "access_denied", message: "Socket authorization is no longer current")
        }
        guard let rawWindow = params["window_id"] as? String, let windowID = UUID(uuidString: rawWindow),
              manager.windowId == windowID,
              AppDelegate.shared?.tabManagerFor(windowId: windowID) === manager else {
            return v2Error(id: id, code: "invalid_params", message: "An exact current window is required")
        }
        let authorized: @MainActor () -> Bool = {
            socketAuthorization.isValid && manager.windowId == windowID
                && AppDelegate.shared?.tabManagerFor(windowId: windowID) === manager
                && self.v2ResolveTabManager(params: params) === manager
        }
        do {
            if request.method == "workspace.organization.plan" {
                guard Set(params.keys).isSubset(of: ["window_id", "workspace_ids"]) else { throw SidebarOrganizationPlanIssuer.Failure.unavailable }
                var workspaceIDs: [UUID]?
                if let value = params["workspace_ids"] {
                    guard let strings = value as? [String], !strings.isEmpty, strings.count <= 256 else { throw SidebarOrganizationPlanIssuer.Failure.unavailable }
                    let ids = strings.compactMap(UUID.init(uuidString:))
                    guard ids.count == strings.count, Set(ids).count == ids.count,
                          ids.allSatisfy({ wanted in manager.tabs.contains { $0.id == wanted } }) else { throw SidebarOrganizationPlanIssuer.Failure.unavailable }
                    workspaceIDs = ids
                }
                let plan = try await manager.sidebarOrganizationCoordinator.preparePlan(
                    tabManager: manager, workspaceIDs: workspaceIDs, authorized: authorized)
                return v2Ok(id: id, result: try organizationObject(plan))
            }
            guard Set(params.keys) == Set(["window_id", "plan_id"]),
                  let text = params["plan_id"] as? String, let planID = UUID(uuidString: text) else {
                return v2Error(id: id, code: "invalid_params", message: "An exact native-issued plan identity is required")
            }
            if request.method == "workspace.organization.apply" {
                let receipt = try await manager.sidebarOrganizationCoordinator.applyPlan(planID,
                    tabManager: manager, authorized: authorized)
                return v2Ok(id: id, result: ["applied": true, "authority": "none", "receipt": try organizationObject(receipt)])
            }
            try manager.sidebarOrganizationCoordinator.rollbackPlan(planID, tabManager: manager, authorized: authorized)
            return v2Ok(id: id, result: ["rolled_back": true, "plan_id": planID.uuidString, "authority": "none"])
        } catch let recovery as SidebarOrganizationPlanCoordinator.RecoveryRequired {
            var diagnostic: [String: Any] = ["plan_id": recovery.planID.uuidString,
                "phase": recovery.phase.rawValue, "before": (try? organizationObject(recovery.before)) ?? [:],
                "observed_available": recovery.observed != nil, "automatic_retry": false, "authority": "none"]
            if let observed = recovery.observed { diagnostic["observed"] = try? organizationObject(observed) }
            if let unavailable = recovery.observedUnavailable { diagnostic["observed_unavailable"] = ["type": unavailable.type, "description": unavailable.description] }
            return v2Error(id: id, code: "recovery_required", message: "Native organization requires recovery review", data: diagnostic)
        } catch let recovery as SidebarOrganizationPlanIssuer.RecoveryRequired {
            let state = recovery.placement
            var diagnostic: [String: Any] = ["plan_id": state.planID.uuidString, "phase": state.phase.rawValue,
                "cause": state.cause.rawValue, "before": (try? organizationObject(state.before)) ?? [],
                "expected": (try? organizationObject(state.expected)) ?? [],
                "observed_available": state.observed != nil, "automatic_retry": false, "authority": "none"]
            diagnostic["observed"] = state.observed.flatMap { try? organizationObject($0) } ?? NSNull()
            return v2Error(id: id, code: "recovery_required", message: "Native placement requires recovery review", data: diagnostic)
        } catch is CancellationError {
            return v2Error(id: id, code: "cancelled", message: "Organization was cancelled")
        } catch {
            return v2Error(id: id, code: "organization_hold", message: "Refresh and review the current native organization plan")
        }
    }

    private func organizationObject<Value: Encodable>(_ value: Value) throws -> Any {
        let data = try JSONEncoder().encode(value)
        guard data.count <= 2 * 1024 * 1024 else { throw SidebarOrganizationPlanIssuer.Failure.unavailable }
        return try JSONSerialization.jsonObject(with: data)
    }
}
