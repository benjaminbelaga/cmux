#!/usr/bin/env python3
"""Compile the actual registry reader and test its bounded command/result seam."""
from pathlib import Path
import json
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''import Foundation
import CmuxFoundation
actor Probe: CommandRunning {
 let output:String
 let mode:String
 var argumentsSeen:[[String]]=[]
 var validBounds=true
 init(_ output:String, mode:String="ok") { self.output=output; self.mode=mode }
 func run(directory:String,executable:String,arguments:[String],timeout:TimeInterval?) async -> CommandResult {
  argumentsSeen.append(arguments)
  if arguments.first == "-c" {
   validBounds = validBounds && timeout == 3
   return .init(stdout:nil,stderr:nil,exitStatus:mode == "python-missing" ? 1 : 0,timedOut:false,executionError:nil)
  }
  validBounds = validBounds && timeout == 20 && directory == "/current/inventory"
  return .init(stdout:output,stderr:mode == "stderr-oversize" ? String(repeating:"x",count:65_537) : nil,
   exitStatus:mode == "nonzero" ? 1 : 0,timedOut:mode == "timeout",executionError:mode == "transport" ? "failed" : nil)
 }
}
@main struct Contract {
 static func main() async throws {
  let hash=String(repeating:"a",count:64)
  let good:[String:Any] = ["schemaVersion":1,"authority":"none","registryFingerprint":hash]
  func encoded(_ value:[String:Any])->String {String(decoding:try! JSONSerialization.data(withJSONObject:value),as:UTF8.self)}
  func service(_ probe:Probe)->SidebarOrganizationRegistryReader {
   SidebarOrganizationRegistryReader(engineURL:URL(fileURLWithPath:"/immutable/session-organization.py"),
    rulesURL:URL(fileURLWithPath:"/current/inventory/session-organization.yaml"),commands:probe,pythonCandidates:["/python"])
  }
  func rejects(_ output:String,mode:String="ok") async -> Bool {
   do {_ = try await service(Probe(output,mode:mode)).read();return false} catch{return true}
  }
  var checks:[String:Bool]=[:]
  let probe=Probe(encoded(good));let reader=service(probe)
  checks["closed current registry result accepted"] = try await reader.read() == hash
  _ = try await reader.read()
  let calls=await probe.argumentsSeen
  checks["every read invokes actual registry mode"] = calls.count == 4 && calls.filter{$0.contains("--registry-fingerprint")}.count == 2
  checks["same immutable code and current canonical rules"] = calls.last == ["/immutable/session-organization.py","--registry-fingerprint","--rules","/current/inventory/session-organization.yaml"]
  checks["no conversation or review input"] = calls.allSatisfy {!$0.contains("--input") && !$0.contains("--review") && !$0.contains("--output") && !$0.contains("--cache")}
  checks["bounded python and engine invocation"] = await probe.validBounds
  for (name,value) in [("schemaVersion",true as Any),("schemaVersion",2 as Any),("authority","admin" as Any),("registryFingerprint",String(repeating:"A",count:64) as Any),("registryFingerprint","short" as Any)] {
   var changed=good;changed[name]=value
   checks["reject "+name+" "+String(describing:value)] = await rejects(encoded(changed))
  }
  var extra=good;extra["review"]="accepted";checks["extra authority rejected"] = await rejects(encoded(extra))
  var missing=good;missing["registryFingerprint"]=nil;checks["missing fingerprint rejected"] = await rejects(encoded(missing))
  checks["malformed output rejected"] = await rejects("not json")
  checks["oversized stdout rejected"] = await rejects(String(repeating:"x",count:65_537))
  for mode in ["python-missing","timeout","nonzero","transport","stderr-oversize"] {
   checks["reject "+mode] = await rejects(encoded(good),mode:mode)
  }
  print(String(decoding:try JSONSerialization.data(withJSONObject:checks,options:.sortedKeys),as:UTF8.self))
 }
}
'''


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=120)


def main():
    with tempfile.TemporaryDirectory(prefix='registry-reader-contract-') as temp:
        p = Path(temp)
        foundation = ROOT/'Packages/macOS/CmuxFoundation/Sources/CmuxFoundation/Process'
        (p/'Runner.swift').write_text('import Foundation\npublic struct CommandRunner: CommandRunning { public init(maximumCaptureBytes:Int?=nil) {}\npublic func run(directory:String,executable:String,arguments:[String],timeout:TimeInterval?) async -> CommandResult { fatalError("test injects actual protocol seam") }}\n')
        run('xcrun', 'swiftc', '-swift-version', '6', '-emit-library', '-emit-module', '-module-name', 'CmuxFoundation', '-emit-module-path', str(p/'CmuxFoundation.swiftmodule'), '-o', str(p/'libCmuxFoundation.dylib'), str(foundation/'CommandRunning.swift'), str(foundation/'CommandResult.swift'), str(p/'Runner.swift'))
        (p/'Contract.swift').write_text(HARNESS)
        run('xcrun', 'swiftc', '-swift-version', '6', '-I', str(p), '-L', str(p), '-lCmuxFoundation', '-Xlinker', '-rpath', '-Xlinker', str(p), str(ROOT/'Sources/SidebarOrganizationRegistryReader.swift'), str(p/'Contract.swift'), '-o', str(p/'contract'))
        checks = json.loads(run(str(p/'contract')).stdout)
        for name, passed in checks.items():
            print(('PASS ' if passed else 'FAIL ')+name)
        print(json.dumps({'passed': sum(checks.values()), 'failed': sum(not x for x in checks.values())}))
        return 0 if len(checks) == 19 and all(checks.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
