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
            run("xcrun", "swiftc", "-swift-version", "6", "-emit-library", "-emit-module", "-module-name", name,
                "-emit-module-path", str(temp / (name + ".swiftmodule")),
                "-o", str(temp / ("lib" + name + ".dylib")),
                *map(str, (ROOT / directory).glob(pattern)))
        entry = temp / "main.swift"
        entry.write_text('''import Foundation
let args = CommandLine.arguments
let encoder = JSONEncoder(); encoder.dateEncodingStrategy = .iso8601
if args[2] == "roundtrip" {
    let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
    let input = try decoder.decode(SidebarOrganizationInput.self, from: Data(contentsOf: URL(fileURLWithPath: args[3])))
    print(String(data: try encoder.encode(input), encoding: .utf8)!)
    print(String(data: try encoder.encode(input.metadata), encoding: .utf8)!)
} else {
    let reader = SidebarOrganizationContextReader(homeDirectory: URL(fileURLWithPath: args[1]))
    let session = SidebarOrganizationInput.Session(toolId: args[2], sessionId: args[3], directory: nil, title: "Fixture", context: nil)
    print(String(data: try encoder.encode(reader.read(session, maximumCharacters: Int(args[4])!)), encoding: .utf8)!)
}
''')
        binary = temp / "reader"
        run("xcrun", "swiftc", "-swift-version", "6", "-I", str(temp), "-L", str(temp),
            "-lCmuxExtensionKit", "-lCmuxSentryScrubbing", "-Xlinker", "-rpath", "-Xlinker", str(temp),
            str(ROOT / "Sources/SidebarOrganizationInput.swift"),
            str(ROOT / "Sources/SidebarOrganizationContextReader.swift"), str(entry), "-o", str(binary))
        environment = {k: v for k, v in os.environ.items() if k not in ("XDG_DATA_HOME", "COMMANDCODE_DIR")}

        def read(tool, sid, limit=6000):
            return json.loads(run(str(binary), str(home), tool, sid, str(limit), env=environment).stdout)

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
        for name, passed in checks:
            print(("PASS " if passed else "FAIL ") + name)
        print(json.dumps({"passed": sum(p for _, p in checks), "failed": sum(not p for _, p in checks)}))
    return 0 if all(p for _, p in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
