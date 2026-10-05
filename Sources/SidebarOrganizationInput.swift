import CmuxExtensionKit
import CmuxSentryScrubbing
import Foundation

/// Bounded native inventory consumed by the registered local organization engine.
struct SidebarOrganizationInput: Codable, Equatable, Sendable {
    struct Session: Codable, Equatable, Sendable {
        let toolId: String
        let sessionId: String
        let directory: String?
        let title: String
        var context: Context?
        var surfaceId: String? = nil
        var processGeneration: UInt64? = nil
    }
    struct Context: Codable, Equatable, Sendable {
        enum Status: String, Codable, Sendable {
            case observed
            case metadataOnly = "metadata-only"
            case unreadable
        }
        struct Message: Codable, Equatable, Sendable {
            let role: String
            let text: String
        }
        let recentMessages: [Message]
        var currentIntent: String? = nil
        var summary: String? = nil
        var compactionSummary: String? = nil
        var scope: String? = nil
        var contextStatus: Status? = nil

        var characterCount: Int {
            recentMessages.reduce(0) { $0 + $1.text.count }
                + [currentIntent, summary, compactionSummary, scope].compactMap { $0 }.reduce(0) { $0 + $1.count }
        }

        /// Bound work before the shared regex scrubber, at a complete token
        /// boundary so truncation cannot expose a partial credential.
        static func scrubbedText(_ value: String, limit: Int, scrubber: SentryScrubber) -> String? {
            guard limit > 0 else { return nil }
            var candidate = String(value.prefix(min(1_500, limit)))
            if candidate.count < value.count {
                guard let boundary = candidate.lastIndex(where: {
                    $0.isWhitespace || !$0.unicodeScalars.allSatisfy({ $0.isASCII })
                }) else { return nil }
                candidate = String(candidate[..<(candidate[boundary].isWhitespace ? boundary : candidate.index(after: boundary))])
            }
            let result = String(scrubber.scrub(candidate).prefix(limit))
            return result.isEmpty ? nil : result
        }

        func bounded(maximumCharacters: Int, homeDirectory: URL) -> Self {
            var remaining = max(0, min(6_000, maximumCharacters))
            var unsafeTextDropped = false
            var budgetTextDropped = false
            let scrubber = SentryScrubber(homeDirectory: homeDirectory.path)
            func text(_ value: String?) -> String? {
                guard let value, remaining > 0 else { return nil }
                guard let result = Self.scrubbedText(value, limit: min(1_500, remaining), scrubber: scrubber) else {
                    if !value.isEmpty {
                        let fullSliceIsSafe = value.count <= 1_500 || value.prefix(1_500).contains(where: {
                            $0.isWhitespace || !$0.unicodeScalars.allSatisfy({ $0.isASCII })
                        })
                        if fullSliceIsSafe { budgetTextDropped = true }
                        else { unsafeTextDropped = true }
                    }
                    return nil
                }
                remaining -= result.count
                return result
            }
            let intent = text(currentIntent)
            let compaction = text(compactionSummary)
            let shortSummary = text(summary)
            let scope = text(scope)
            var messages: [Message] = []
            for message in recentMessages.reversed() where ["user", "assistant"].contains(message.role) {
                guard messages.count < 8, remaining > 0 else { break }
                if let value = text(message.text) { messages.append(.init(role: message.role, text: value)) }
            }
            guard !unsafeTextDropped else { return .init(recentMessages: [], contextStatus: .unreadable) }
            if budgetTextDropped && messages.isEmpty && [intent, compaction, shortSummary, scope].allSatisfy({ $0 == nil }) {
                return .init(recentMessages: [], contextStatus: .metadataOnly)
            }
            return .init(recentMessages: messages.reversed(), currentIntent: intent, summary: shortSummary,
                         compactionSummary: compaction, scope: scope, contextStatus: contextStatus)
        }
    }
    /// Descriptive data only: no retained request, source binding or opener authority.
    struct SourceReference: Codable, Equatable, Sendable {
        let provider: String
        let accountRef: String
        let directoryUserId: String
        let resourceId: String
        let messageId: String?
        let evidenceFingerprint: String
    }
    struct ServiceObservation: Codable, Equatable, Sendable {
        let service: String
        let surfaceId: String
        let kind: String
        let observedAt: Date
    }
    struct Workspace: Codable, Equatable, Sendable {
        let id: String
        let title: String
        let revision: UInt64
        let groupId: String?
        let tags: [CmuxSidebarContextTag]
        let aliases: [String]
        let summary: String?
        let rejectedAutomaticTagIDs: [String]
        let rejectedSourceFingerprints: [String]
        var sessions: [Session]
        var groupName: String? = nil
        var groupOrigin: String? = nil
        var sourceReferences: [SourceReference]? = nil
        var serviceObservations: [ServiceObservation]? = nil

        /// Enrichment is descriptive and removable; native inventory metadata
        /// stays byte-for-byte comparable with a fresh native snapshot.
        mutating func boundEnrichment(maximumCharacters: Int, homeDirectory: URL) -> Int {
            var remaining = max(0, maximumCharacters)
            let scrubber = SentryScrubber(homeDirectory: homeDirectory.path)
            func text(_ value: String?, limit: Int) -> String? {
                guard let value, remaining > 0 else { return nil }
                guard let result = Context.scrubbedText(value, limit: min(limit, remaining), scrubber: scrubber) else { return nil }
                remaining -= result.count
                return result
            }
            groupName = text(groupName, limit: 512)
            groupOrigin = text(groupOrigin, limit: 64)
            sourceReferences = sourceReferences.map { references in
                references.prefix(32).filter { reference in
                    let values = [reference.provider, reference.accountRef, reference.directoryUserId,
                                  reference.resourceId, reference.messageId ?? "", reference.evidenceFingerprint]
                    let limits = [64, 200, 200, 200, 200, 64]
                    guard zip(values, limits).allSatisfy({ $0.count <= $1 && scrubber.scrub($0) == $0 }) else { return false }
                    let count = values.reduce(0) { $0 + $1.count }
                    guard count <= remaining else { return false }
                    remaining -= count
                    return true
                }
            }
            serviceObservations = serviceObservations.map { observations in
                observations.prefix(32).filter { observation in
                    let values = [observation.service, observation.surfaceId, observation.kind]
                    guard observation.service == "gmail", UUID(uuidString: observation.surfaceId) != nil,
                          ["browser-origin", "gmail-tool"].contains(observation.kind),
                          values.allSatisfy({ scrubber.scrub($0) == $0 }) else { return false }
                    // The ISO8601 date is enrichment too, and consumes the same budget.
                    let count = values.reduce(0) { $0 + $1.count } + 24
                    guard count <= remaining else { return false }
                    remaining -= count
                    return true
                }
            }
            return max(0, maximumCharacters) - remaining
        }
    }
    let schemaVersion = 1
    let id: UUID
    let windowID: UUID?
    let createdAt: Date
    var workspaces: [Workspace]

    var metadata: Self {
        var result = self
        for workspace in result.workspaces.indices {
            result.workspaces[workspace].groupName = nil
            result.workspaces[workspace].groupOrigin = nil
            result.workspaces[workspace].sourceReferences = nil
            result.workspaces[workspace].serviceObservations = nil
            for session in result.workspaces[workspace].sessions.indices { result.workspaces[workspace].sessions[session].context = nil }
        }
        return result
    }

    var isValid: Bool {
        !workspaces.isEmpty && workspaces.count <= 256
            && Set(workspaces.map(\.id)).count == workspaces.count
            && workspaces.allSatisfy { UUID(uuidString: $0.id) != nil && $0.title.count <= 512 && $0.sessions.count <= 64 }
    }
}
