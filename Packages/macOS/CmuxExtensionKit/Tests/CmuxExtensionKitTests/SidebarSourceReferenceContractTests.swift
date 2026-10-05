import Foundation
import Testing
@_spi(CmuxHostTransport) @testable import CmuxExtensionKit

@Suite("Native source navigation transport")
struct SidebarSourceReferenceContractTests {
    private let workspaceID = UUID(uuidString: "11111111-1111-1111-1111-111111111111")!
    private let fingerprint = String(repeating: "a", count: 64)

    @Test
    func deliberateSourceClickRoundTripsItsExactNativeFence() throws {
        let action = CmuxSidebarAction.openSourceReference(workspaceID: workspaceID,
            expectedRevision: 42, referenceFingerprint: fingerprint)
        #expect(try CmuxSidebarXPCCodec.decodeAction(CmuxSidebarXPCCodec.encodeAction(action)) == action)
        #expect(action.requiredScopes == [.openURL])
    }

    @Test
    @MainActor
    func sourceHelperUsesExistingReplyAndPermissionGate() async throws {
        var requests: [CmuxSidebarAction] = []
        let host = CmuxSidebarHost(performAction: { action, reply in
            requests.append(action)
            reply(action.requiredScopes.isSubset(of: [.openURL]) ? .accepted : .rejected("Denied"))
        })
        try await host.openSourceReference(workspaceID: workspaceID,
            expectedRevision: 42, referenceFingerprint: fingerprint)
        #expect(requests == [.openSourceReference(workspaceID: workspaceID,
            expectedRevision: 42, referenceFingerprint: fingerprint)])
    }

    @Test
    @MainActor
    func contextEditingPermissionDoesNotGrantSourceOpening() async {
        let host = CmuxSidebarHost(performAction: { action, reply in
            let grants: Set<CmuxExtensionActionScope> = [.editWorkspaceContext]
            reply(action.requiredScopes.isSubset(of: grants) ? .accepted : .rejected("Opening denied"))
        })
        do {
            try await host.openSourceReference(workspaceID: workspaceID,
                expectedRevision: 42, referenceFingerprint: fingerprint)
            Issue.record("Source opening must retain the native action reply gate")
        } catch {
            #expect(error as? CmuxSidebarActionError == .rejected("Opening denied"))
        }
    }
}
