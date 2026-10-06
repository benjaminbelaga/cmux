import Foundation

/// The accepted socket connection's existing listener and password revision
/// fence. It carries no new credential or workspace authority.
struct SocketCommandAuthorization: Sendable {
    @TaskLocal static var current: Self?
    let isCurrent: @Sendable () -> Bool

    var isValid: Bool { !Task.isCancelled && isCurrent() }
}
