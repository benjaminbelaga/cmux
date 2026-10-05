import CmuxExtensionKit
import CmuxFoundation
import Foundation

/// Read-only canonical resolver. A returned locator still requires the native
/// caller's current workspace/binding check; this component never opens it.
actor SidebarSourceReferenceResolver {
    enum Failure: Error { case invalidRequest, transport, invalidResponse, held }
    struct Request: Codable, Equatable, Sendable {
        let requestID: String
        let workspaceID: UUID
        let expectedRevision: UInt64
        let sourceReference: CmuxSidebarSourceReference
        enum CodingKeys: String, CodingKey {
            case requestID = "request_id", workspaceID = "workspace_id"
            case expectedRevision = "expected_revision", sourceReference = "source_reference"
        }
    }
    struct Locator: Codable, Equatable, Sendable {
        let provider: String
        let principal: String
        let workspaceID: String
        let directoryUserID: String
        let accountRef: String
        let gmailThreadKey: String
        let gmailThreadID: String
        let gmailMessageID: String
        let url: String
        enum CodingKeys: String, CodingKey {
            case provider, principal, url
            case workspaceID = "workspace_id", directoryUserID = "directory_user_id"
            case accountRef = "account_ref", gmailThreadKey = "gmail_thread_key"
            case gmailThreadID = "gmail_thread_id", gmailMessageID = "gmail_message_id"
        }
    }
    static func validate(_ data: Data, request: Request, now: Date = Date()) throws -> Locator {
        throw Failure.invalidResponse
    }
}
