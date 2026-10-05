import Foundation
import CmuxSentryScrubbing
import SQLite3

/// Reads only an exact native SID, with bounded tails and read-only databases.
struct SidebarOrganizationContextReader: Sendable {
    let homeDirectory: URL
    var environment: [String: String] = ProcessInfo.processInfo.environment

    func read(_ session: SidebarOrganizationInput.Session, maximumCharacters: Int) -> SidebarOrganizationInput.Context? {
        guard maximumCharacters > 0 else { return nil }
        let tool = session.toolId
        let budget = min(6_000, maximumCharacters)
        if ["qwen", "qwen_code", "qwen-code"].contains(tool) {
            return readQwen(session.sessionId, maximumCharacters: budget)
        }
        if ["kimi", "kimi_code", "kimi-code"].contains(tool) {
            return readKimi(session.sessionId, maximumCharacters: budget)
        }
        if ["opencode", "opencode-go", "opencode_go"].contains(tool) {
            return readOpenCode(session.sessionId, maximumCharacters: budget)
        }
        guard UUID(uuidString: session.sessionId) != nil else { return nil }
        let root: URL
        switch tool {
        case "codex": root = homeDirectory.appendingPathComponent(".codex/sessions")
        case "claude", "claude_code": root = homeDirectory.appendingPathComponent(".claude/projects")
        case "commandcode", "command_code":
            root = environment["COMMANDCODE_DIR"].flatMap { $0.isEmpty ? nil : URL(fileURLWithPath: $0) }?
                .appendingPathComponent("projects") ?? homeDirectory.appendingPathComponent(".commandcode/projects")
        default: return nil
        }
        guard !containsSymbolicLink(root) else { return nil }
        guard let enumeration = FileManager.default.enumerator(at: root,
            includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey], options: [.skipsHiddenFiles]) else { return nil }
        var candidates: [URL] = []
        var scanned = 0
        while let url = enumeration.nextObject() as? URL {
            scanned += 1
            guard scanned <= 10_000 else { return nil }
            if enumeration.level > 5 { return nil }
            if (try? url.resourceValues(forKeys: [.isSymbolicLinkKey]))?.isSymbolicLink == true {
                enumeration.skipDescendants(); continue
            }
            guard url.pathExtension == "jsonl",
                  url.deletingPathExtension().lastPathComponent == session.sessionId
                    || (tool == "codex" && url.lastPathComponent.hasSuffix("-" + session.sessionId + ".jsonl")),
                  let values = try? url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey]),
                  values.isRegularFile == true, values.isSymbolicLink != true else { continue }
            candidates.append(url)
        }
        guard candidates.count == 1, let url = candidates.first,
              let handle = try? FileHandle(forReadingFrom: url) else { return nil }
        defer { try? handle.close() }
        let commandCode = ["commandcode", "command_code"].contains(tool)
        if tool == "codex" || commandCode {
            guard let head = try? handle.read(upToCount: 8_192),
                  let line = head.split(separator: 10).first,
                  let object = try? JSONSerialization.jsonObject(with: Data(line)) as? [String: Any],
                  object["type"] as? String == (commandCode ? "session" : "session_meta"),
                  (commandCode ? object["id"] as? String : (object["payload"] as? [String: Any])?["id"] as? String)
                    == session.sessionId else { return nil }
        }
        guard let length = try? handle.seekToEnd(),
              (try? handle.seek(toOffset: length > 2_097_152 ? length - 2_097_152 : 0)) != nil,
              let data = try? handle.read(upToCount: 2_097_152) else { return nil }
        var messages: [SidebarOrganizationInput.Context.Message] = []
        var remaining = budget
        for line in data.split(separator: 10).reversed() {
            guard messages.count < 8, remaining > 0 else { break }
            guard let object = try? JSONSerialization.jsonObject(with: Data(line)) as? [String: Any] else { continue }
            let payload: [String: Any]
            if tool == "codex" {
                guard object["type"] as? String == "response_item", let value = object["payload"] as? [String: Any], value["type"] as? String == "message" else { continue }
                payload = value
            } else if commandCode {
                guard object["type"] as? String == "message", let value = object["message"] as? [String: Any] else { continue }
                payload = value
            } else {
                guard object["sessionId"] as? String == session.sessionId, let value = object["message"] as? [String: Any] else { continue }
                payload = value
            }
            guard let role = payload["role"] as? String, ["user", "assistant"].contains(role) else { continue }
            let parts = payload["content"] as? [[String: Any]] ?? []
            let text = payload["content"] as? String ?? parts.compactMap { $0["text"] as? String }.joined(separator: "\n")
            let bounded = String(SentryScrubber(homeDirectory: homeDirectory.path).scrub(text).prefix(min(1_500, remaining)))
            guard !bounded.isEmpty else { continue }
            messages.append(.init(role: role, text: bounded))
            remaining -= bounded.count
        }
        return messages.isEmpty ? nil : .init(recentMessages: messages.reversed())
    }

    /// Unsupported harnesses and exhausted budgets are metadata-only; a supported
    /// store that cannot establish exact identity is unreadable, never guessed.
    func context(for session: SidebarOrganizationInput.Session, maximumCharacters: Int) -> SidebarOrganizationInput.Context {
        let supported = ["qwen", "qwen_code", "qwen-code", "kimi", "kimi_code", "kimi-code",
                         "opencode", "opencode-go", "opencode_go", "codex", "claude", "claude_code",
                         "commandcode", "command_code"].contains(session.toolId)
        guard supported, maximumCharacters > 0 else {
            return .init(recentMessages: [], contextStatus: .metadataOnly)
        }
        guard var result = read(session, maximumCharacters: maximumCharacters) else {
            return .init(recentMessages: [], contextStatus: .unreadable)
        }
        result.contextStatus = .observed
        return result.bounded(maximumCharacters: maximumCharacters, homeDirectory: homeDirectory)
    }

    private func safeSID(_ sid: String) -> Bool {
        !sid.isEmpty && sid.count <= 128 && sid.utf8.allSatisfy {
            (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 45 || $0 == 95
        }
    }

    private func boundedData(_ url: URL, maximumBytes: Int, tail: Bool = false) -> Data? {
        guard !containsSymbolicLink(url),
              (try? url.resourceValues(forKeys: [.isRegularFileKey]))?.isRegularFile == true,
              let handle = try? FileHandle(forReadingFrom: url) else { return nil }
        defer { try? handle.close() }
        guard let length = try? handle.seekToEnd(), tail || length <= maximumBytes,
              (try? handle.seek(toOffset: tail && length > maximumBytes ? length - UInt64(maximumBytes) : 0)) != nil,
              var data = try? handle.read(upToCount: maximumBytes) else { return nil }
        // A bounded tail may start inside a JSON record. Never decode that fragment.
        if tail && length > maximumBytes {
            guard let newline = data.firstIndex(of: 10) else { return nil }
            data = Data(data.suffix(from: data.index(after: newline)))
        }
        return data
    }

    private func records(_ url: URL) -> [[String: Any]]? {
        guard let data = boundedData(url, maximumBytes: 2_097_152, tail: true) else { return nil }
        let lines = data.split(separator: 10)
        guard lines.count <= 10_000 else { return nil }
        var result: [[String: Any]] = []
        for line in lines {
            guard let value = try? JSONSerialization.jsonObject(with: Data(line)) as? [String: Any] else { return nil }
            result.append(value)
        }
        return result
    }

    private func readQwen(_ sid: String, maximumCharacters: Int) -> SidebarOrganizationInput.Context? {
        guard safeSID(sid) else { return nil }
        let root = homeDirectory.appendingPathComponent(".qwen/projects")
        guard !containsSymbolicLink(root), let enumeration = FileManager.default.enumerator(at: root,
            includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey], options: [.skipsHiddenFiles]) else { return nil }
        var candidates: [URL] = []
        var scanned = 0
        while let url = enumeration.nextObject() as? URL {
            scanned += 1
            guard scanned <= 10_000, enumeration.level <= 5 else { return nil }
            if (try? url.resourceValues(forKeys: [.isSymbolicLinkKey]))?.isSymbolicLink == true {
                enumeration.skipDescendants(); continue
            }
            if url.lastPathComponent == sid + ".jsonl", url.deletingLastPathComponent().lastPathComponent == "chats" {
                candidates.append(url)
            }
        }
        guard candidates.count == 1, let url = candidates.first, let rows = records(url) else { return nil }
        var byID: [String: [String: Any]] = [:]
        var leaf: String?
        for row in rows {
            guard row["sessionId"] as? String == sid, let id = row["uuid"] as? String, !id.isEmpty,
                  let type = row["type"] as? String else { return nil }
            if let parent = row["parentUuid"], !(parent is NSNull), !(parent is String) { return nil }
            if let prior = byID[id] {
                // Qwen streams fragments under the same UUID. Only compatible
                // identities may concatenate; a conflicting branch is held.
                guard prior["type"] as? String == type,
                      prior["parentUuid"] as? String == row["parentUuid"] as? String else { return nil }
                var merged = prior
                if var message = prior["message"] as? [String: Any], let next = row["message"] as? [String: Any] {
                    message["parts"] = (message["parts"] as? [[String: Any]] ?? []) + (next["parts"] as? [[String: Any]] ?? [])
                    merged["message"] = message
                }
                byID[id] = merged
            } else { byID[id] = row }
            if type == "user" || type == "assistant" { leaf = id }
        }
        guard var current = leaf else { return nil }
        var chain: [[String: Any]] = []
        var visited = Set<String>()
        while true {
            guard visited.insert(current).inserted, let row = byID[current] else { return nil }
            chain.append(row)
            guard let parent = row["parentUuid"] as? String else { break }
            guard !parent.isEmpty else { return nil }
            current = parent
        }
        let ordered = chain.reversed()
        var messages: [SidebarOrganizationInput.Context.Message] = []
        var compaction: String?
        var scope: String?
        for row in ordered {
            if let cwd = row["cwd"] as? String { scope = cwd }
            if row["type"] as? String == "system", row["subtype"] as? String == "chat_compression",
               let payload = row["systemPayload"] as? [String: Any],
               let info = payload["info"] as? [String: Any], info["compressionStatus"] as? Int == 1,
               let history = payload["compressedHistory"] as? [[String: Any]] {
                // Only the canonical compression snapshot is descriptive context.
                let snapshots = history.flatMap { $0["parts"] as? [[String: Any]] ?? [] }
                    .compactMap { $0["text"] as? String }.filter { $0.contains("<state_snapshot>") && $0.contains("</state_snapshot>") }
                compaction = snapshots.last
            }
            guard let type = row["type"] as? String, ["user", "assistant"].contains(type),
                  let message = row["message"] as? [String: Any] else { continue }
            let text = (message["parts"] as? [[String: Any]] ?? []).compactMap { $0["text"] as? String }.joined(separator: "\n")
            if !text.isEmpty { messages.append(.init(role: type, text: text)) }
        }
        return SidebarOrganizationInput.Context(recentMessages: messages,
            currentIntent: messages.last(where: { $0.role == "user" })?.text,
            compactionSummary: compaction, scope: scope, contextStatus: .observed)
            .bounded(maximumCharacters: maximumCharacters, homeDirectory: homeDirectory)
    }

    private func readKimi(_ sid: String, maximumCharacters: Int) -> SidebarOrganizationInput.Context? {
        guard safeSID(sid) else { return nil }
        let root = homeDirectory.appendingPathComponent(".kimi-code")
        guard let index = records(root.appendingPathComponent("session_index.jsonl")) else { return nil }
        let matches = index.filter { $0["sessionId"] as? String == sid }
        guard let entry = matches.last, entry["deleted"] as? Bool != true,
              let path = entry["sessionDir"] as? String else { return nil }
        let directory = URL(fileURLWithPath: path).standardizedFileURL
        let sessions = root.appendingPathComponent("sessions").standardizedFileURL
        guard directory.path.hasPrefix(sessions.path + "/"), directory.lastPathComponent == sid,
              !containsSymbolicLink(directory),
              let data = boundedData(directory.appendingPathComponent("state.json"), maximumBytes: 262_144),
              let state = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        if let identity = state["id"] { guard identity as? String == sid else { return nil } }
        let legacy = state["id"] == nil && state["version"] == nil
        guard let agents = state["agents"] as? [String: Any], let main = agents["main"] as? [String: Any],
              let homedir = main["homedir"] as? String,
              main["parentAgentId"] == nil || main["parentAgentId"] is NSNull else { return nil }
        let mainHome = URL(fileURLWithPath: homedir).standardizedFileURL
        guard mainHome == directory.appendingPathComponent("agents/main").standardizedFileURL,
              let rows = records(mainHome.appendingPathComponent("wire.jsonl")) else { return nil }
        var messages: [SidebarOrganizationInput.Context.Message] = []
        for row in rows {
            guard let type = row["type"] as? String else { return nil }
            if let agent = row["agentId"] as? String { if agent != "main" { continue } }
            else if !legacy && type.hasPrefix("context.") { return nil }
            guard type.hasPrefix("context.") else { continue }
            if type == "context.clear" { messages.removeAll(); continue }
            // Undo/splice/compaction need a separately verified replay model.
            // Holding an unsupported mutation is safer than showing stale text.
            guard type == "context.append_message" else { return nil }
            guard let message = row["message"] as? [String: Any], let role = message["role"] as? String,
                  ["user", "assistant"].contains(role) else { continue }
            let text = (message["content"] as? [[String: Any]] ?? []).filter { $0["type"] as? String == "text" }
                .compactMap { $0["text"] as? String }.joined(separator: "\n")
            if !text.isEmpty { messages.append(.init(role: role, text: text)) }
        }
        return SidebarOrganizationInput.Context(recentMessages: messages,
            currentIntent: state["lastPrompt"] as? String, scope: state["cwd"] as? String ?? entry["workDir"] as? String,
            contextStatus: .observed).bounded(maximumCharacters: maximumCharacters, homeDirectory: homeDirectory)
    }

    private func readOpenCode(_ sid: String, maximumCharacters: Int) -> SidebarOrganizationInput.Context? {
        guard sid.hasPrefix("ses_"), sid.count <= 128,
              sid.utf8.allSatisfy({ (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 95 }) else { return nil }
        let dataHome = environment["XDG_DATA_HOME"].flatMap { $0.isEmpty ? nil : URL(fileURLWithPath: $0) }
            ?? homeDirectory.appendingPathComponent(".local/share")
        let url = dataHome.appendingPathComponent("opencode/opencode.db")
        guard !containsSymbolicLink(url),
              (try? url.resourceValues(forKeys: [.isRegularFileKey]))?.isRegularFile == true else { return nil }
        var database: OpaquePointer?
        guard sqlite3_open_v2(url.path, &database, SQLITE_OPEN_READONLY | SQLITE_OPEN_FULLMUTEX, nil) == SQLITE_OK,
              let database else {
            if let database { sqlite3_close(database) }
            return nil
        }
        defer { sqlite3_close(database) }
        sqlite3_busy_timeout(database, 100)
        // Bound cells before JSON decoding; parameters bind every native SID relation.
        let sql = """
        SELECT m.id, m.data, p.data FROM session s
        JOIN message m ON m.session_id = s.id
        JOIN part p ON p.message_id = m.id AND p.session_id = s.id
        WHERE s.id = ? AND length(CAST(m.data AS BLOB)) <= 65536
            AND length(CAST(p.data AS BLOB)) <= 65536
        ORDER BY m.time_created DESC, m.id DESC, p.id DESC LIMIT 64
        """
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(database, sql, -1, &statement, nil) == SQLITE_OK, let statement else { return nil }
        defer { sqlite3_finalize(statement) }
        let bound = sid.withCString { sqlite3_bind_text(statement, 1, $0, -1, unsafeBitCast(-1, to: sqlite3_destructor_type.self)) }
        guard bound == SQLITE_OK else { return nil }
        var messages: [(id: String, role: String, text: String)] = []
        var remaining = maximumCharacters
        var step = sqlite3_step(statement)
        while step == SQLITE_ROW {
            guard let idBytes = sqlite3_column_text(statement, 0),
                  let messageBytes = sqlite3_column_text(statement, 1),
                  let partBytes = sqlite3_column_text(statement, 2),
                  let message = try? JSONSerialization.jsonObject(with: Data(bytes: messageBytes, count: Int(sqlite3_column_bytes(statement, 1)))) as? [String: Any],
                  let role = message["role"] as? String, ["user", "assistant"].contains(role),
                  let part = try? JSONSerialization.jsonObject(with: Data(bytes: partBytes, count: Int(sqlite3_column_bytes(statement, 2)))) as? [String: Any],
                  part["type"] as? String == "text", let text = part["text"] as? String else {
                step = sqlite3_step(statement); continue
            }
            let id = String(cString: idBytes)
            let existing = messages.last?.id == id
            guard remaining > 0, existing || messages.count < 8 else { break }
            let messageBudget = 1_500 - (existing ? messages.last!.text.count : 0)
            let bounded = String(SentryScrubber(homeDirectory: homeDirectory.path).scrub(text).prefix(min(messageBudget, remaining)))
            if !bounded.isEmpty {
                if existing {
                    let last = messages.removeLast()
                    messages.append((id, role, bounded + last.text))
                } else { messages.append((id, role, bounded)) }
                remaining -= bounded.count
            }
            step = sqlite3_step(statement)
        }
        guard step == SQLITE_DONE || step == SQLITE_ROW else { return nil }
        return messages.isEmpty ? nil : .init(recentMessages: messages.reversed().map { .init(role: $0.role, text: $0.text) })
    }

    private func containsSymbolicLink(_ url: URL) -> Bool {
        var component = url.standardizedFileURL
        // The injected home is the trusted boundary (macOS temporary homes may
        // sit under /var, an OS symlink). Reject links within the store itself.
        while component.path != "/" && component.path != homeDirectory.standardizedFileURL.path {
            if (try? component.resourceValues(forKeys: [.isSymbolicLinkKey]))?.isSymbolicLink == true { return true }
            component.deleteLastPathComponent()
        }
        return false
    }
}
