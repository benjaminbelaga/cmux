import AppKit
import CmuxExtensionKit
import Foundation

/// Native navigation attachment and deliberate opening. Neither a source label
/// nor a caller request ID authenticates a mailbox; the canonical resolver does.
@MainActor
final class SidebarSourceReferenceCoordinator {
    enum Failure: Error { case missingWorkspace, revisionConflict, missingBinding, invalidReference, readbackMismatch, openingFailed }
    typealias Request = SidebarSourceReferenceResolver.Request
    typealias Locator = SidebarSourceReferenceResolver.Locator
    private let resolveSource: @Sendable (Request) async throws -> Locator
    private let openURL: @MainActor (URL) -> Bool

    init(resolver: SidebarSourceReferenceResolver = SidebarSourceReferenceResolver(),
         openURL: @escaping @MainActor (URL) -> Bool = { NSWorkspace.shared.open($0) }) {
        resolveSource = { try await resolver.resolve($0) }
        self.openURL = openURL
    }

    init(resolveSource: @escaping @Sendable (Request) async throws -> Locator,
         openURL: @escaping @MainActor (URL) -> Bool) {
        self.resolveSource = resolveSource
        self.openURL = openURL
    }

    /// The retained CaseOS request is verified before any native context write.
    func attach(manager: TabManager, workspaceID: UUID, expectedRevision: UInt64,
                requestID: String, reference: CmuxSidebarSourceReference,
                authorized: @MainActor () -> Bool = { true }) async throws -> UInt64 {
        try Task.checkCancellation()
        guard authorized() else { throw CancellationError() }
        let workspace = try current(manager, workspaceID, expectedRevision)
        let before = workspace.workspaceContext.context
        let request = Request(requestID: requestID, workspaceID: workspaceID,
                              expectedRevision: expectedRevision, sourceReference: reference)
        let locator = try await resolveSource(request)
        try Task.checkCancellation()
        guard authorized() else { throw CancellationError() }
        try verifyLocator(locator, reference: reference)
        guard try current(manager, workspaceID, expectedRevision) === workspace,
              workspace.workspaceContext.context == before else { throw Failure.revisionConflict }
        let revision = try workspace.workspaceContext.attachSourceReference(
            expectedRevision: expectedRevision, reference: reference, requestID: requestID)
        guard manager.tabs.contains(where: { $0 === workspace }),
              workspace.workspaceContext.context.revision == revision,
              workspace.workspaceContext.context.sourceReferences?.contains(reference) == true,
              workspace.workspaceContext.context.sourceRequestBindings?.contains(where: {
                  $0.requestId == requestID && $0.sourceReferenceFingerprint == reference.fingerprint
                      && $0.attachedRevision <= revision
              }) == true else { throw Failure.readbackMismatch }
        return revision
    }

    /// Resolves only the native-owned request binding captured by this click.
    /// A later manual revision is allowed, but must match the click exactly.
    func open(manager: TabManager, workspaceID: UUID, expectedRevision: UInt64,
              referenceFingerprint: String,
              authorized: @MainActor () -> Bool = { true }) async throws {
        try Task.checkCancellation()
        guard authorized() else { throw CancellationError() }
        let workspace = try current(manager, workspaceID, expectedRevision)
        let before = workspace.workspaceContext.context
        let references = (before.sourceReferences ?? []).filter { $0.fingerprint == referenceFingerprint }
        let bindings = (before.sourceRequestBindings ?? []).filter { $0.sourceReferenceFingerprint == referenceFingerprint }
        guard references.count == 1, bindings.count == 1,
              bindings[0].attachedRevision <= expectedRevision else { throw Failure.missingBinding }
        let reference = references[0]
        let request = Request(requestID: bindings[0].requestId, workspaceID: workspaceID,
                              expectedRevision: expectedRevision, sourceReference: reference)
        let locator = try await resolveSource(request)
        try Task.checkCancellation()
        guard authorized() else { throw CancellationError() }
        try verifyLocator(locator, reference: reference)
        guard try current(manager, workspaceID, expectedRevision) === workspace,
              workspace.workspaceContext.context == before else { throw Failure.revisionConflict }
        guard let url = URL(string: locator.url), openURL(url) else { throw Failure.openingFailed }
    }

    private func current(_ manager: TabManager, _ id: UUID, _ revision: UInt64) throws -> Workspace {
        guard let workspace = manager.tabs.first(where: { $0.id == id }) else { throw Failure.missingWorkspace }
        guard workspace.workspaceContext.context.revision == revision else { throw Failure.revisionConflict }
        return workspace
    }

    private func verifyLocator(_ locator: Locator, reference: CmuxSidebarSourceReference) throws {
        guard reference.isStructurallyValid, reference.messageId != nil,
              locator.provider == "gmail", locator.accountRef == reference.accountRef,
              locator.directoryUserID == reference.directoryUserId,
              locator.gmailThreadKey == reference.resourceId,
              locator.gmailMessageID == reference.messageId,
              !locator.principal.isEmpty, let url = URL(string: locator.url),
              url.scheme == "https", url.host == "mail.google.com",
              url.user == nil, url.password == nil, url.port == nil else { throw Failure.invalidReference }
    }
}
