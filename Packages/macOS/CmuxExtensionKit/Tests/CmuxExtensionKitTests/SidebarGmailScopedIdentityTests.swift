import Foundation
import Testing
@testable import CmuxExtensionKit

@Suite("Canonical Gmail scoped identity relation")
struct SidebarGmailScopedIdentityTests {
    struct Vector: Sendable {
        let name: String
        let workspaceId: String
        let principal: String
        let threadId: String
        let accountRef: String
        let resourceId: String
        let mailboxJSONUTF8Hex: String
        let threadJSONUTF8Hex: String

        var reference: CmuxSidebarSourceReference {
            .init(accountRef: accountRef, directoryUserId: "123", resourceId: resourceId,
                  messageId: "message123", evidenceFingerprint: String(repeating: "0", count: 64))
        }
    }

    // Independent fixtures from CaseOS scoped_key: UTF-8 JSON, no aliases or normalization.
    static let vectors: [Vector] = [
        .init(name: "ordinary", workspaceId: "yoyaku.fr",
              principal: "ben@yoyaku.fr", threadId: "thread123",
              accountRef: "MBX-f5d7581d7c7b6c27474d160b86132e57fd0bf2fa4eb2cd8e20c7e7f730bc3656",
              resourceId: "GTK-ebbd622c1037e77820879bf447dd78dbad90d9f72326e78c70f1fa1bd48c1392",
              mailboxJSONUTF8Hex: "5b22796f79616b752e6672222c2262656e40796f79616b752e6672225d",
              threadJSONUTF8Hex: "5b22796f79616b752e6672222c2262656e40796f79616b752e6672222c22746872656164313233225d"),
        .init(name: "slash-localpart", workspaceId: "yoyaku.fr",
              principal: "ben/team@yoyaku.fr", threadId: "thread123",
              accountRef: "MBX-8eb9e3bab564203982ca3e56631fb8c93179712c47bbb7df7170bcf55746c3aa",
              resourceId: "GTK-a2cd23a6b5c8c3b81cf0fed40c2197865103a104b4a7a4f8173e84e2453cc41b",
              mailboxJSONUTF8Hex: "5b22796f79616b752e6672222c2262656e2f7465616d40796f79616b752e6672225d",
              threadJSONUTF8Hex: "5b22796f79616b752e6672222c2262656e2f7465616d40796f79616b752e6672222c22746872656164313233225d"),
        .init(name: "secondary-domain", workspaceId: "yoyaku.fr",
              principal: "ben@secondary.example", threadId: "thread123",
              accountRef: "MBX-2cc2e5bcb2476dfd68af858cb070ddaefc811648d88480a1e722c6e4bebc69a5",
              resourceId: "GTK-b6a528b9df6e6df97e7c143f773a60e798cce981254cda921ad87dc1af8018c9",
              mailboxJSONUTF8Hex: "5b22796f79616b752e6672222c2262656e407365636f6e646172792e6578616d706c65225d",
              threadJSONUTF8Hex: "5b22796f79616b752e6672222c2262656e407365636f6e646172792e6578616d706c65222c22746872656164313233225d"),
        .init(name: "unicode-canonical", workspaceId: "tenant-é",
              principal: "bé[email]@example.test", threadId: "fil-☕",
              accountRef: "MBX-942dad9d93ef43d8639180f432b007c2e3527ea7c9edcca9e32336ef9c56228e",
              resourceId: "GTK-398a1439fc2a680465d21408b7f851b5e01b0f5a4f8f9678417b8eadf472c162",
              mailboxJSONUTF8Hex: "5b2274656e616e742dc3a9222c2262c3a95b656d61696c5d406578616d706c652e74657374225d",
              threadJSONUTF8Hex: "5b2274656e616e742dc3a9222c2262c3a95b656d61696c5d406578616d706c652e74657374222c2266696c2de29895225d"),
        .init(name: "unicode-unescaped", workspaceId: "tenant-東京",
              principal: "é😀@example.test", threadId: "thread/with\\escaped\"parts",
              accountRef: "MBX-532c5e941910c8f592b99767f1694aea81374385f5c2d40485e7ebfed70c67cc",
              resourceId: "GTK-d41b8cf237f447b4976a8edf894203bb1a937e4021a0a4ce7cf50c91c6f7d127",
              mailboxJSONUTF8Hex: "5b2274656e616e742de69db1e4baac222c22c3a9f09f9880406578616d706c652e74657374225d",
              threadJSONUTF8Hex: "5b2274656e616e742de69db1e4baac222c22c3a9f09f9880406578616d706c652e74657374222c227468726561642f776974685c5c657363617065645c227061727473225d")
    ]

    @Test(arguments: vectors)
    func pythonCanonicalVectorsMatch(vector: Vector) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.withoutEscapingSlashes]
        let mailbox = try encoder.encode([vector.workspaceId, vector.principal])
        let thread = try encoder.encode([vector.workspaceId, vector.principal, vector.threadId])
        #expect(mailbox.map { String(format: "%02x", $0) }.joined() == vector.mailboxJSONUTF8Hex)
        #expect(thread.map { String(format: "%02x", $0) }.joined() == vector.threadJSONUTF8Hex)
        #expect(vector.reference.matchesGmailScope(workspaceId: vector.workspaceId,
            principal: vector.principal, threadId: vector.threadId))
    }

    @Test(arguments: vectors)
    func aDifferentPrincipalCannotReuseOpaqueKeys(vector: Vector) {
        #expect(!vector.reference.matchesGmailScope(workspaceId: vector.workspaceId,
            principal: "other@domain.example", threadId: vector.threadId))
    }

    @Test(arguments: vectors)
    func aDifferentTenantCannotReuseOpaqueKeys(vector: Vector) {
        #expect(!vector.reference.matchesGmailScope(workspaceId: "other-tenant.example",
            principal: vector.principal, threadId: vector.threadId))
    }

    @Test(arguments: vectors)
    func aDifferentThreadCannotReuseOpaqueKeys(vector: Vector) {
        #expect(!vector.reference.matchesGmailScope(workspaceId: vector.workspaceId,
            principal: vector.principal, threadId: "otherThread"))
    }

    @Test(arguments: vectors)
    func anIndependentMailboxDigestIsRejected(vector: Vector) {
        var reference = vector.reference
        reference.accountRef = "MBX-" + String(repeating: "f", count: 64)
        #expect(!reference.matchesGmailScope(workspaceId: vector.workspaceId,
            principal: vector.principal, threadId: vector.threadId))
    }

    @Test(arguments: vectors)
    func anIndependentThreadDigestIsRejected(vector: Vector) {
        var reference = vector.reference
        reference.resourceId = "GTK-" + String(repeating: "f", count: 64)
        #expect(!reference.matchesGmailScope(workspaceId: vector.workspaceId,
            principal: vector.principal, threadId: vector.threadId))
    }

    @Test
    func principalIsAlreadyCanonicalAndNeverNormalized() {
        let vector = Self.vectors[0]
        #expect(!vector.reference.matchesGmailScope(workspaceId: vector.workspaceId,
            principal: "BEN@YOYAKU.FR", threadId: vector.threadId))
        #expect(!vector.reference.matchesGmailScope(workspaceId: vector.workspaceId,
            principal: " " + vector.principal, threadId: vector.threadId))
    }

    @Test
    func emptyAndOversizedComponentsAreRejected() {
        let vector = Self.vectors[0]
        let tuple = [vector.workspaceId, vector.principal, vector.threadId]
        for index in tuple.indices {
            for replacement in ["", String(repeating: "x", count: 1_001)] {
                var changed = tuple
                changed[index] = replacement
                #expect(!vector.reference.matchesGmailScope(workspaceId: changed[0],
                    principal: changed[1], threadId: changed[2]))
            }
        }
    }

    @Test
    func utf8ByteBoundUsesIndependentMatchingKeys() {
        let limit = String(repeating: "é", count: 500)
        #expect(limit.utf8.count == 1_000)
        let within = CmuxSidebarSourceReference(accountRef: "MBX-9ea1bdeed72eae8f610d0b55ff8a55a68dd87b3f2122926334c51150d604d930",
            directoryUserId: "123", resourceId: "GTK-05f835ea6a64a3029b9eb7ab06b3743fa4f0c1115f2d0827c4b6bb24636d257e",
            evidenceFingerprint: String(repeating: "0", count: 64))
        #expect(within.matchesGmailScope(workspaceId: "tenant", principal: limit, threadId: "thread"))
        let over = CmuxSidebarSourceReference(accountRef: "MBX-bd1142e68fbb5bbfd1e275786c98c9c0f53b6297ff37e632bb0fe55c751c8bb6",
            directoryUserId: "123", resourceId: "GTK-339fa7f44da8fd67adf1a3a8c07ef169cad3ddf46bd7236aa8ca72194bb45263",
            evidenceFingerprint: String(repeating: "0", count: 64))
        #expect(!over.matchesGmailScope(workspaceId: "tenant", principal: limit + "a", threadId: "thread"))
    }

    @Test
    func invalidSourceMetadataStillFailsClosed() {
        let vector = Self.vectors[0]
        var reference = vector.reference
        reference.provider = "drive"
        #expect(!reference.matchesGmailScope(workspaceId: vector.workspaceId,
            principal: vector.principal, threadId: vector.threadId))
        reference = vector.reference
        reference.directoryUserId = "not-a-directory-id"
        #expect(!reference.matchesGmailScope(workspaceId: vector.workspaceId,
            principal: vector.principal, threadId: vector.threadId))
        reference = vector.reference
        reference.evidenceFingerprint = "short"
        #expect(!reference.matchesGmailScope(workspaceId: vector.workspaceId,
            principal: vector.principal, threadId: vector.threadId))
    }

    @Test
    func aMatchingTupleDoesNotAuthenticateDirectoryOrEvidence() {
        let vector = Self.vectors[0]
        var reference = vector.reference
        reference.directoryUserId = "456"
        reference.evidenceFingerprint = String(repeating: "a", count: 64)
        reference.messageId = nil
        #expect(reference.matchesGmailScope(workspaceId: vector.workspaceId,
            principal: vector.principal, threadId: vector.threadId))
        // Directory UID, current provider evidence and native authorization remain caller gates.
    }
}
