#!/usr/bin/env python3
"""Exercise the real bounded reader on verified Qwen/Kimi storage fixtures.

Only the Foundation leaf is compiled; no app, model, real transcript or installer
is used. Fixture UUIDs and content are generated independently of live sessions.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    kwargs.setdefault("timeout", 90)
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)


def main():
    checks = []

    def check(name, passed):
        checks.append((name, bool(passed)))

    with tempfile.TemporaryDirectory(prefix="cmux-context-readers-") as temporary:
        temp = Path(temporary)
        home = temp / "home"
        home.mkdir()
        for name, directory, pattern in [
            ("CmuxExtensionKit", "Packages/macOS/CmuxExtensionKit/Sources/CmuxExtensionKit/Sidebar", "CmuxSidebarContextTag*.swift"),
            ("CmuxSentryScrubbing", "Packages/Shared/CmuxSentryTelemetry/Sources/CmuxSentryScrubbing", "*.swift"),
        ]:
            sources = list((ROOT / directory).glob(pattern))
            if name == "CmuxExtensionKit":
                sources.append(ROOT / directory / "CmuxSidebarWorkspaceContextProposal.swift")
            run("xcrun", "swiftc", "-swift-version", "6", "-emit-library", "-emit-module", "-module-name", name,
                "-emit-module-path", str(temp / (name + ".swiftmodule")),
                "-o", str(temp / ("lib" + name + ".dylib")),
                *map(str, sources))
        foundation = ROOT / "Packages/macOS/CmuxFoundation/Sources/CmuxFoundation/Process"
        stub = temp / "CommandRunner.swift"
        stub.write_text('''import Foundation
public struct CommandRunner: CommandRunning {
    public init(maximumCaptureBytes: Int) {}
    public func run(directory: String, executable: String, arguments: [String], timeout: TimeInterval?) async -> CommandResult {
        .init(stdout: nil, stderr: nil, exitStatus: nil, timedOut: false, executionError: "Fixture must inject fake")
    }
}
''')
        run("xcrun", "swiftc", "-swift-version", "6", "-emit-library", "-emit-module", "-module-name", "CmuxFoundation",
            "-emit-module-path", str(temp / "CmuxFoundation.swiftmodule"), "-o", str(temp / "libCmuxFoundation.dylib"),
            str(foundation / "CommandRunning.swift"), str(foundation / "CommandResult.swift"), str(stub))
        entry = temp / "main.swift"
        entry.write_text('''import Foundation
import CmuxFoundation
struct FakeCommands: CommandRunning {
    let output: String
    func run(directory: String, executable: String, arguments: [String], timeout: TimeInterval?) async -> CommandResult {
        if let index = arguments.firstIndex(of: "--output") {
            do { try Data(output.utf8).write(to: URL(fileURLWithPath: arguments[index + 1])) }
            catch { return .init(stdout: nil, stderr: nil, exitStatus: nil, timedOut: false, executionError: "Fixture write") }
        }
        return .init(stdout: "", stderr: "", exitStatus: 0, timedOut: false, executionError: nil)
    }
}
@main struct Fixture {
static func main() async throws {
let args = CommandLine.arguments
let encoder = JSONEncoder(); encoder.dateEncodingStrategy = .iso8601
if ["roundtrip", "prepare", "analyze"].contains(args[2]) {
    let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
    let input = try decoder.decode(SidebarOrganizationInput.self, from: Data(contentsOf: URL(fileURLWithPath: args[3])))
    let service = SidebarOrganizationService(commands: FakeCommands(output: args.count > 4 ? args[4] : "{}"),
        homeDirectory: URL(fileURLWithPath: args[1]), temporaryDirectory: URL(fileURLWithPath: args[1]), pythonCandidates: ["fixture"])
    if args[2] == "analyze" {
        do { print(String(data: try encoder.encode(try await service.analyze(input)), encoding: .utf8)!) }
        catch { print("null") }
    } else {
        let result = args[2] == "prepare" ? try await service.prepare(input) : input
        print(String(data: try encoder.encode(result), encoding: .utf8)!)
        print(String(data: try encoder.encode(result.metadata), encoding: .utf8)!)
    }
} else {
    let reader = SidebarOrganizationContextReader(homeDirectory: URL(fileURLWithPath: args[1]))
    let tool = args[2].replacingOccurrences(of: "status:", with: "")
    let session = SidebarOrganizationInput.Session(toolId: tool, sessionId: args[3], directory: nil, title: "Fixture", context: nil)
    if args[2].hasPrefix("status:") {
        print(String(data: try encoder.encode(reader.context(for: session, maximumCharacters: Int(args[4])!)), encoding: .utf8)!)
    } else { print(String(data: try encoder.encode(reader.read(session, maximumCharacters: Int(args[4])!)), encoding: .utf8)!) }
}
}
}
''')
        binary = temp / "reader"
        run("xcrun", "swiftc", "-swift-version", "6", "-I", str(temp), "-L", str(temp),
            "-lCmuxExtensionKit", "-lCmuxSentryScrubbing", "-lCmuxFoundation", "-parse-as-library", "-Xlinker", "-rpath", "-Xlinker", str(temp),
            str(ROOT / "Sources/SidebarOrganizationInput.swift"),
            str(ROOT / "Sources/SidebarOrganizationContextReader.swift"),
            str(ROOT / "Sources/SidebarOrganizationOutput.swift"),
            str(ROOT / "Sources/SidebarOrganizationAnalyzing.swift"),
            str(ROOT / "Sources/SidebarOrganizationService.swift"), str(entry), "-o", str(binary))
        environment = {k: v for k, v in os.environ.items() if k not in ("XDG_DATA_HOME", "COMMANDCODE_DIR")}

        def read(tool, sid, limit=6000):
            return json.loads(run(str(binary), str(home), tool, sid, str(limit), env=environment, timeout=8).stdout)

        def jsonl(path, rows):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))

        def texts(value):
            return [message["text"] for message in (value or {}).get("recentMessages", [])]

        sid = str(uuid.uuid4())
        first, dead, rewind, latest = (str(uuid.uuid4()) for _ in range(4))
        qwen = home / ".qwen/projects/fixture/chats" / (sid + ".jsonl")

        def qrow(identity, parent, kind, text=None):
            result = {"uuid": identity, "parentUuid": parent, "sessionId": sid, "type": kind,
                      "cwd": "/fixture", "timestamp": "2026-01-01T00:00:00Z"}
            if text is not None:
                result["message"] = {"role": kind, "parts": [{"text": text}]}
            return result

        rows = [qrow(first, None, "user", "Retained instruction"),
                qrow(dead, first, "assistant", "Dead branch must not leak"),
                dict(qrow(rewind, first, "system"), subtype="rewind"),
                qrow(latest, rewind, "user", "Current instruction")]
        jsonl(qwen, rows)
        original = qwen.read_bytes()
        check("Qwen exact SID active ancestry", texts(read("qwen", sid)) == ["Retained instruction", "Current instruction"])
        check("Qwen alias same exact store", texts(read("qwen_code", sid)) == ["Retained instruction", "Current instruction"])
        check("Qwen source unchanged", qwen.read_bytes() == original)
        jsonl(qwen, rows[:3])
        check("Qwen terminal rewind selects actual canonical leaf", texts(read("qwen", sid)) == ["Retained instruction"])
        snapshot = "<state_snapshot>Confirmed compacted intent</state_snapshot>"
        compression = dict(qrow(str(uuid.uuid4()), latest, "system"), subtype="chat_compression",
                           systemPayload={"info": {"compressionStatus": 1}, "compressedHistory": [{"role": "user", "parts": [{"text": snapshot}]}]})
        jsonl(qwen, [*rows, compression])
        check("Qwen terminal compression snapshot observed", (read("qwen", sid) or {}).get("compactionSummary") == snapshot)
        jsonl(qwen, rows)
        check("Qwen missing exact SID held", read("qwen", str(uuid.uuid4())) is None)
        jsonl(qwen, [*rows, qrow(str(uuid.uuid4()), "missing-parent", "user", "Unverified branch")])
        check("Qwen broken ancestry held", read("qwen", sid) is None)
        jsonl(qwen, [*rows, dict(qrow(str(uuid.uuid4()), latest, "user", "Foreign SID"), sessionId=str(uuid.uuid4()))])
        check("Qwen mixed SID held", read("qwen", sid) is None)
        jsonl(qwen, rows)
        duplicate = home / ".qwen/projects/other/chats" / (sid + ".jsonl")
        jsonl(duplicate, rows)
        check("Qwen duplicate exact SID held", read("qwen", sid) is None)
        duplicate.unlink()
        fragment = qrow(latest, rewind, "user", "Second fragment")
        jsonl(qwen, [*rows, fragment])
        check("Qwen compatible stream fragments aggregate", texts(read("qwen", sid))[-1] == "Current instruction\nSecond fragment")
        jsonl(qwen, [*rows, dict(fragment, parentUuid=dead)])
        check("Qwen conflicting stream fragments held", read("qwen", sid) is None)
        jsonl(qwen, [qrow(first, latest, "user", "Cycle"), qrow(latest, first, "assistant", "Cycle")])
        check("Qwen ancestry cycle held", read("qwen", sid) is None)
        jsonl(qwen, [qrow(first, 42, "user", "Wrong parent shape")])
        check("Qwen invalid parent shape held", read("qwen", sid) is None)
        no_parent = qrow(first, None, "user", "Missing canonical identity field")
        del no_parent["parentUuid"]
        jsonl(qwen, [no_parent])
        check("Qwen absent parent identity held", read("qwen", sid) is None)
        jsonl(qwen, [rows[0], dict(qrow(dead, first, "assistant", "Unknown record"), type="unverified-type")])
        check("Qwen unknown record type held", read("qwen", sid) is None)
        model = qrow(dead, first, "assistant", "Model role response")
        model["message"]["role"] = "model"
        jsonl(qwen, [rows[0], model, dict(qrow(rewind, dead, "system"), subtype="session_artifact_event")])
        check("Qwen canonical model role and artifact leaf", texts(read("qwen", sid)) == ["Retained instruction", "Model role response"])
        qwen.unlink()
        qwen.symlink_to(duplicate)
        check("Qwen symbolic link held", read("qwen", sid) is None)
        qwen.unlink()
        jsonl(qwen, rows)
        kimi_sid = str(uuid.uuid4())
        session = home / ".kimi-code/sessions/fixture" / kimi_sid
        main_home = session / "agents/main"
        main_home.mkdir(parents=True)
        index = home / ".kimi-code/session_index.jsonl"
        jsonl(index, [{"sessionId": kimi_sid, "sessionDir": str(session), "workDir": "/fixture"}])
        state = {"id": kimi_sid, "version": 1, "lastPrompt": "Current intent", "cwd": "/fixture",
                 "agents": {"main": {"homedir": str(main_home), "type": "main", "parentAgentId": None}}}
        state_path = session / "state.json"
        state_path.write_text(json.dumps(state))
        wire = main_home / "wire.jsonl"
        messages = [{"type": "context.append_message", "agentId": "main", "message": {"role": role,
                     "content": [{"type": "text", "text": text}]}} for role, text in
                    [("user", "Exact user intent"), ("assistant", "Exact response")]]
        jsonl(wire, [*messages, {"type": "context.append_message", "agentId": "child",
              "message": {"role": "user", "content": [{"type": "text", "text": "Child must not leak"}]}}])
        original_state, original_wire, original_index = state_path.read_bytes(), wire.read_bytes(), index.read_bytes()
        check("Kimi canonical main agent only", texts(read("kimi", kimi_sid)) == ["Exact user intent", "Exact response"])
        check("Kimi stores unchanged", (state_path.read_bytes(), wire.read_bytes(), index.read_bytes()) == (original_state, original_wire, original_index))
        state_path.write_text(json.dumps({k: v for k, v in state.items() if k not in ("id", "version")}))
        jsonl(wire, [{k: v for k, v in message.items() if k != "agentId"} for message in messages])
        check("Kimi legacy exact index main store", texts(read("kimi", kimi_sid)) == ["Exact user intent", "Exact response"])
        jsonl(wire, [dict(messages[0], agentId=42)])
        check("Kimi legacy explicit non-string agent identity held", read("kimi", kimi_sid) is None)
        state_path.write_text(json.dumps(dict(state, id=str(uuid.uuid4()))))
        check("Kimi explicit wrong state SID held", read("kimi", kimi_sid) is None)
        state_path.write_text(json.dumps(state))
        foreign = temp / "foreign"
        foreign.mkdir()
        state_path.write_text(json.dumps(dict(state, agents={"main": {"homedir": str(foreign), "type": "main", "parentAgentId": None}})))
        check("Kimi main store escape held", read("kimi", kimi_sid) is None)
        state_path.write_text(json.dumps(state))
        jsonl(wire, [{"type": "context.append_message", "agentId": "main", "message": {"role": "user",
              "content": [{"type": "text", "text": "person@example.com password=secretvalue " + "x" * 8000}]}}])
        result = read("kimi", kimi_sid, 100)
        all_text = "".join(texts(result))
        check("Kimi scrubbed bounded text", bool(all_text) and len(all_text) <= 100 and "person@example.com" not in all_text and "secretvalue" not in all_text)
        check("Invalid path SID held", read("kimi", "../other") is None)
        jsonl(wire, [*messages, {"type": "context.clear", "agentId": "main"}, messages[-1]])
        check("Kimi context clear removes stale messages", texts(read("kimi", kimi_sid)) == ["Exact response"])
        jsonl(wire, [*messages, {"type": "context.undo", "agentId": "main"}])
        check("Kimi unsupported replay mutation held", read("kimi", kimi_sid) is None)
        check("Supported unreadable status truthful", read("status:kimi", kimi_sid)["contextStatus"] == "unreadable")
        jsonl(wire, messages)
        jsonl(index, [{"sessionId": kimi_sid, "sessionDir": str(session)}, {"sessionId": kimi_sid, "deleted": True}])
        check("Kimi latest index deletion held", read("kimi", kimi_sid) is None)
        jsonl(index, [{"sessionId": kimi_sid, "sessionDir": str(session), "workDir": "/fixture"}])
        check("Supported observed status truthful", read("status:kimi", kimi_sid)["contextStatus"] == "observed")
        check("Unsupported tool stays metadata-only", read("status:unknown", kimi_sid)["contextStatus"] == "metadata-only")
        check("Exhausted budget stays metadata-only", read("status:kimi", kimi_sid, 0)["contextStatus"] == "metadata-only")

        native_surface = str(uuid.uuid4())
        workspace = {"id": str(uuid.uuid4()), "title": "Stable native title", "revision": 7,
                     "groupId": str(uuid.uuid4()), "tags": [], "aliases": ["Stable alias"], "summary": "Stable accepted summary",
                     "rejectedAutomaticTagIDs": [], "rejectedSourceFingerprints": [],
                     "groupName": "person@example.com password=secretvalue Folder", "groupOrigin": "manual",
                     "sourceReferences": [{"provider": "gmail", "accountRef": "fixture", "directoryUserId": "42",
                          "resourceId": "GTK-" + "1" * 64, "evidenceFingerprint": "2" * 64}],
                     "serviceObservations": [{"service": "gmail", "surfaceId": native_surface, "kind": "browser-origin",
                          "observedAt": "2026-01-01T00:00:00Z"}],
                     "sessions": [{"toolId": "unknown", "sessionId": str(uuid.uuid4()), "title": "Fixture",
                          "surfaceId": native_surface, "processGeneration": 4, "context": {
                              "recentMessages": [{"role": "system", "text": "Excluded authority"},
                                                 {"role": "user", "text": "person@example.com password=secretvalue Useful intent"}],
                              "currentIntent": "person@example.com password=secretvalue Current intent",
                              "summary": "person@example.com Summary", "compactionSummary": "password=secretvalue Snapshot",
                              "scope": "/Users/fixture/work", "contextStatus": "observed"}}]}
        input_path = temp / "input.json"
        def invocation(mode, value, *arguments):
            input_path.write_text(json.dumps(value))
            return [json.loads(line) for line in run(str(binary), str(home), mode, str(input_path), *arguments,
                      env=environment, timeout=8).stdout.splitlines()]

        packet = {"id": str(uuid.uuid4()), "createdAt": "2026-01-01T00:00:00Z", "workspaces": [workspace]}
        roundtrip, metadata = invocation("roundtrip", packet)
        check("Optional enrichment round trips", roundtrip["workspaces"][0]["sourceReferences"] == workspace["sourceReferences"]
              and roundtrip["workspaces"][0]["sessions"][0]["context"]["currentIntent"].endswith("Current intent"))
        stripped = metadata["workspaces"][0]
        check("Metadata strips every enrichment", not any(k in stripped for k in ("groupName", "groupOrigin", "sourceReferences", "serviceObservations"))
              and "context" not in stripped["sessions"][0])
        check("Metadata preserves native SID surface generation revision", stripped["revision"] == 7 and stripped["sessions"][0]["surfaceId"] == native_surface
              and stripped["sessions"][0]["processGeneration"] == 4 and stripped["sessions"][0]["sessionId"] == workspace["sessions"][0]["sessionId"])
        prepared, prepared_metadata = invocation("prepare", packet)
        serialized = json.dumps(prepared)
        check("Prepare scrubs all supplied optional text", "person@example.com" not in serialized and "secretvalue" not in serialized and "/Users/fixture" not in serialized)
        check("Prepare preserves stable native metadata", prepared_metadata == metadata)
        check("Prepare excludes system message", all(m["role"] in ("user", "assistant") for m in prepared["workspaces"][0]["sessions"][0]["context"]["recentMessages"]))
        legacy_workspace = {k: v for k, v in workspace.items() if k not in ("groupName", "groupOrigin", "sourceReferences", "serviceObservations")}
        legacy_workspace["sessions"] = [{k: v for k, v in workspace["sessions"][0].items() if k != "context"}]
        legacy_packet = dict(packet, workspaces=[legacy_workspace])
        check("Legacy input decodes without optional fields", invocation("roundtrip", legacy_packet)[0]["workspaces"][0]["revision"] == 7)
        check("Prepare unsupported context remains metadata-only", invocation("prepare", legacy_packet)[0]["workspaces"][0]["sessions"][0]["context"]["contextStatus"] == "metadata-only")
        large_context = {"recentMessages": [{"role": "user", "text": "Useful word " * 200} for _ in range(8)],
                         "currentIntent": "Intent " * 300, "summary": "Summary " * 300,
                         "compactionSummary": "Snapshot " * 300, "scope": "Scope " * 300}
        large_workspace = dict(workspace, sourceReferences=workspace["sourceReferences"] * 100,
                               serviceObservations=workspace["serviceObservations"] * 100,
                               sessions=[dict(workspace["sessions"][0], sessionId=str(uuid.uuid4()), context=large_context) for _ in range(5)])
        bounded = invocation("prepare", dict(packet, workspaces=[large_workspace]))[0]["workspaces"][0]
        def context_count(context):
            return sum(len(m["text"]) for m in context.get("recentMessages", [])) + sum(len(context.get(k, "")) for k in ("currentIntent", "summary", "compactionSummary", "scope"))
        context_counts = [context_count(s["context"]) for s in bounded["sessions"]]
        extra = sum(len(bounded.get(k, "")) for k in ("groupName", "groupOrigin"))
        extra += sum(sum(len(v) for v in r.values()) for r in bounded.get("sourceReferences", []))
        extra += sum(sum(len(r[k]) for k in ("service", "surfaceId", "kind")) + 24 for r in bounded.get("serviceObservations", []))
        check("Session budget includes all optional text", all(count <= 6000 for count in context_counts))
        check("Workspace budget includes enrichment and contexts", extra + sum(context_counts) <= 12000)
        check("Optional reference observation arrays capped", len(bounded["sourceReferences"]) <= 32 and len(bounded["serviceObservations"]) <= 32)
        check("Exhausted later session status truthful", bounded["sessions"][-1]["context"]["contextStatus"] == "metadata-only")
        output = {"schemaVersion": 1, "proposals": [], "diagnostics": []}
        check("Legacy output missing registry permits tags", invocation("analyze", legacy_packet, json.dumps(output))[0] is not None)
        check("Actual output registry preserved", invocation("analyze", legacy_packet, json.dumps(dict(output, registryFingerprint="a" * 64)))[0]["registryFingerprint"] == "a" * 64)
        check("Invalid registry output held", invocation("analyze", legacy_packet, json.dumps(dict(output, registryFingerprint="invalid")))[0] is None)
        for name, passed in checks:
            print(("PASS " if passed else "FAIL ") + name)
        print(json.dumps({"passed": sum(p for _, p in checks), "failed": sum(not p for _, p in checks)}))
    return 0 if all(p for _, p in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
