import CryptoKit
import Foundation

public extension CmuxSidebarSourceReference {
    /// Compares this reference with the canonical CaseOS Gmail tuple keys.
    ///
    /// This is a deterministic identity relation, not mailbox authentication or
    /// navigation authorization. The caller supplies the exact canonical
    /// Directory principal; this method never normalizes aliases or case.
    /// Directory identity, current provider evidence and native grants remain
    /// separate caller checks.
    ///
    /// - Parameter workspaceId: The Google tenant scope, not a native workspace UUID.
    /// - Parameter principal: The already canonical Directory principal.
    /// - Parameter threadId: The provider thread identifier in that mailbox.
    /// - Returns: Whether both opaque keys match the exact bounded tuple.
    ///
    /// ```swift
    /// let matches = reference.matchesGmailScope(workspaceId: googleTenant,
    ///     principal: canonicalPrincipal, threadId: gmailThreadId)
    /// ```
    func matchesGmailScope(workspaceId: String, principal: String, threadId: String) -> Bool {
        let parts = [workspaceId, principal, threadId]
        guard isStructurallyValid,
              parts.allSatisfy({ !$0.isEmpty && $0.utf8.count <= 1_000 }) else { return false }

        // Match scoped_key's UTF-8 compact JSON arrays without escaping slashes.
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.withoutEscapingSlashes]
        guard let mailbox = try? encoder.encode([workspaceId, principal]),
              let thread = try? encoder.encode(parts) else { return false }
        let mailboxKey = "MBX-" + SHA256.hash(data: mailbox).map { String(format: "%02x", $0) }.joined()
        let threadKey = "GTK-" + SHA256.hash(data: thread).map { String(format: "%02x", $0) }.joined()
        return accountRef == mailboxKey && resourceId == threadKey
    }
}
