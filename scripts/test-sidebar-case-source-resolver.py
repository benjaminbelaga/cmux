#!/usr/bin/env python3
"""Actual Swift leaf/SDK contracts with controlled commands; no app or mail.

Compilation must wait for the parent native build slot. This does not exercise
AppKit composition, retained native bindings, provider auth or browser opening.
"""
from pathlib import Path
import json
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''import Foundation
import CmuxExtensionKit
import CmuxFoundation

actor Probe: CommandRunning {
 var calls=0
 var validInvocation=false
 var packetDirectory:String?
 var started=false
 var continuation:CheckedContinuation<Void,Never>?
 let mode:String
 let response:Data
 init(mode:String="ok",response:Data) { self.mode=mode;self.response=response }
 func release() { continuation?.resume();continuation=nil }
 func run(directory:String,executable:String,arguments:[String],timeout:TimeInterval?) async -> CommandResult {
  calls += 1;packetDirectory=directory
  let file=URL(fileURLWithPath:arguments.last ?? "")
  let dm=(try? FileManager.default.attributesOfItem(atPath:directory)[.posixPermissions] as? NSNumber)?.intValue
  let fm=(try? FileManager.default.attributesOfItem(atPath:file.path)[.posixPermissions] as? NSNumber)?.intValue
  let input=try? Data(contentsOf:file)
  let object=input.flatMap {try? JSONSerialization.jsonObject(with:$0) as? [String:Any]}
  let keys=Set(object?.keys.map{$0} ?? [])
  let prepare=arguments.first == "case-source-prepare"
  let expected=prepare ? Set(["case_id","window_id","workspace_id","expected_revision"]) : Set(["window_id","workspace_id","expected_revision","case_binding","source_reference"])
  validInvocation = executable == "/fixture/.local/share/yoyaku-case-os-source-view/current/.venv/bin/case-os" && arguments.count == 2 && (prepare || arguments.first == "case-source-resolve") && file.deletingLastPathComponent().path == directory && timeout == 65 && dm == 0o700 && fm == 0o600 && keys == expected && (input?.count ?? 9_000) <= 8_192
  if mode == "blocked" {
   await withCheckedContinuation { continuation in self.continuation=continuation;self.started=true }
  }
  if mode == "timeout" { return .init(stdout:nil,stderr:nil,exitStatus:nil,timedOut:true,executionError:nil) }
  if mode == "failed" { return .init(stdout:nil,stderr:"controlled",exitStatus:1,timedOut:false,executionError:nil) }
  var value=(try? JSONSerialization.jsonObject(with:response) as? [String:Any]) ?? [:]
  if value["observed_at"] != nil { value["observed_at"]=ISO8601DateFormatter().string(from:Date()) }
  let output=mode == "oversize" ? String(repeating:"x",count:65_537) : mode == "malformed" ? "{" : String(decoding:(try? JSONSerialization.data(withJSONObject:value)) ?? Data(),as:UTF8.self)
  let error=mode == "stderr" ? String(repeating:"x",count:65_537) : nil
  return .init(stdout:output,stderr:error,exitStatus:0,timedOut:false,executionError:nil)
 }
}

@main struct Contract {
 static func main() async throws {
  typealias Resolver=SidebarCaseSourceResolver
  let window=UUID(),workspace=UUID(),caseID="CASE-2026-10-06-ABCDEF12",eventID="evt:mail-source.1"
  let ref=CmuxSidebarSourceReference(accountRef:"MBX-"+String(repeating:"a",count:64),directoryUserId:"123",resourceId:"GTK-"+String(repeating:"b",count:64),messageId:"message123",evidenceFingerprint:String(repeating:"c",count:64))
  let binding=Resolver.Binding(caseId:caseID,sourceEventId:eventID,sourceRevision:String(repeating:"d",count:64),sourceReferenceFingerprint:ref.fingerprint!,attachedRevision:7)
  let prepare=Resolver.PrepareRequest(caseID:caseID,windowID:window,workspaceID:workspace,expectedRevision:7)
  let resolve=Resolver.ResolveRequest(windowID:window,workspaceID:workspace,expectedRevision:7,caseBinding:binding,sourceReference:ref)
  let now=Date(timeIntervalSince1970:1791240000),formatter=ISO8601DateFormatter()
  let locator:[String:Any] = ["provider":"gmail","principal":"ben@yoyaku.fr","workspace_id":"yoyaku.fr","directory_user_id":"123","account_ref":ref.accountRef,"gmail_thread_key":ref.resourceId,"gmail_thread_id":"thread","gmail_message_id":"message123","url":"https://mail.google.com/mail/?authuser=ben%40yoyaku.fr#all/thread"]
  let good:[String:Any] = ["status":"verified","verified":true,"case_id":caseID,"source_event_id":eventID,"source_revision":binding.sourceRevision,"source_reference":try JSONSerialization.jsonObject(with:JSONEncoder().encode(ref)),"source_reference_fingerprint":ref.fingerprint!,"window_id":window.uuidString,"workspace_id":workspace.uuidString,"expected_revision":7,"observed_at":formatter.string(from:now),"locator":locator,"source_provenance":"Existing canonical source with current Directory/provider proof.","business_authority":"none","native_provider_session_binding":false,"model_calls":0,"sends":0,"drafts":0,"next_action":"Native must recheck before attaching or opening."]
  let hold:[String:Any] = ["status":"source_reference_hold","verified":false,"reason":"native_pair_not_live","model_calls":0,"sends":0,"drafts":0,"next_action":"Reconcile the same source."]
  var checks:[String:Bool]=[:]
  func accepted(_ object:[String:Any], resolving:Bool=false)->Bool {
   do {
    let data=try JSONSerialization.data(withJSONObject:object)
    let outcome=resolving ? try Resolver.validate(data,resolve:resolve,now:now) : try Resolver.validate(data,prepare:prepare,now:now)
    if case .verified = outcome { return true };return false
   } catch { return false }
  }
  checks["prepare exact closed locator accepted"] = accepted(good)
  checks["resolve exact binding and ref accepted"] = accepted(good,resolving:true)
  for (name,value) in [("case_id","CASE-2026-10-06-00000000"),("source_event_id","invalid event"),("source_revision","invalid"),("window_id",UUID().uuidString),("workspace_id",UUID().uuidString),("source_reference_fingerprint",String(repeating:"e",count:64)),("business_authority","admin")] {
   var changed=good;changed[name]=value;checks["reject changed "+name] = !accepted(changed)
  }
  for name in ["expected_revision","model_calls","sends","drafts"] {
   var changed=good;changed[name]=name == "expected_revision" ? 8 : 1;checks["reject positive or changed "+name] = !accepted(changed)
   changed=good;changed[name]=true;checks["reject boolean "+name] = !accepted(changed)
  }
  for name in ["verified","native_provider_session_binding"] {
   var changed=good;changed[name]=name == "verified" ? false : true;checks["reject changed "+name] = !accepted(changed)
  }
  for (name,value) in [("url","https://example.com"),("principal","ben@yoyaku.fr/foreign"),("directory_user_id","999"),("account_ref","MBX-foreign"),("gmail_thread_key","GTK-foreign"),("gmail_message_id","foreign"),("gmail_thread_id","thread/foreign"),("workspace_id","tenant with spaces"),("provider","other")] {
   var changed=good,nested=locator;nested[name]=value;changed["locator"]=nested;checks["reject locator "+name] = !accepted(changed)
  }
  var extra=good;extra["model_prompt"]="untrusted";checks["reject extra response field"] = !accepted(extra)
  extra=good;var nested=locator;nested["admin_grant"]=true;extra["locator"]=nested;checks["reject extra locator field"] = !accepted(extra)
  extra=good;var source=try JSONSerialization.jsonObject(with:JSONEncoder().encode(ref)) as! [String:Any];source["grant"]="open";extra["source_reference"]=source;checks["reject extra source ref field"] = !accepted(extra)
  var missing=good;missing.removeValue(forKey:"source_provenance");checks["reject missing success field"] = !accepted(missing)
  for seconds in [-121.0,6.0] {
   var changed=good;changed["observed_at"]=formatter.string(from:now.addingTimeInterval(seconds));checks["reject stale/future "+String(seconds)] = !accepted(changed)
  }
  var drift=good;drift["source_event_id"]="evt-other";checks["resolve event identity cannot change"] = !accepted(drift,resolving:true)
  drift=good;drift["source_revision"]=String(repeating:"e",count:64);checks["resolve source revision cannot change"] = !accepted(drift,resolving:true)
  var other=ref;other.evidenceFingerprint=String(repeating:"e",count:64)
  drift=good;drift["source_reference"]=try JSONSerialization.jsonObject(with:JSONEncoder().encode(other));drift["source_reference_fingerprint"]=other.fingerprint!
  checks["resolve cannot substitute coherent different ref"] = !accepted(drift,resolving:true)
  do {
   let outcome=try Resolver.validate(JSONSerialization.data(withJSONObject:hold),prepare:prepare,now:now)
   if case .held(let value) = outcome { checks["closed reference hold accepted without locator"] = value.reason == "native_pair_not_live" }
  } catch { checks["closed reference hold accepted without locator"] = false }
  var transport=hold;transport["status"]="source_transport_hold"
  do { if case .held = try Resolver.validate(JSONSerialization.data(withJSONObject:transport),resolve:resolve,now:now) { checks["closed transport hold accepted"] = true } } catch { checks["closed transport hold accepted"] = false }
  for name in ["locator","source_reference","business_authority"] {
   var invalid=hold;invalid[name]="unexpected"
   do { _ = try Resolver.validate(JSONSerialization.data(withJSONObject:invalid),prepare:prepare,now:now);checks["hold rejects "+name] = false } catch { checks["hold rejects "+name] = true }
  }
  let response=try JSONSerialization.data(withJSONObject:good)
  for resolving in [false,true] {
   let probe=Probe(response:response)
   let actual=Resolver(commands:probe,homeDirectory:URL(fileURLWithPath:"/fixture"))
   let outcome=resolving ? try await actual.resolve(resolve) : try await actual.prepare(prepare)
   let verified:Bool;if case .verified = outcome { verified=true } else { verified=false }
   let directory=await probe.packetDirectory!
   checks["fixed private command "+String(resolving)] = await probe.validInvocation && verified
   checks["cleanup successful private packet "+String(resolving)] = !FileManager.default.fileExists(atPath:directory)
  }
  for mode in ["timeout","failed","oversize","stderr","malformed"] {
   let probe=Probe(mode:mode,response:response),service=Resolver(commands:probe,homeDirectory:URL(fileURLWithPath:"/fixture"))
   var refused=false;do { _ = try await service.prepare(prepare) } catch { refused=true }
   let directory=await probe.packetDirectory!
   checks["refuse and clean "+mode] = refused && !FileManager.default.fileExists(atPath:directory)
  }
  let heldProbe=Probe(response:try JSONSerialization.data(withJSONObject:hold)),heldService=Resolver(commands:heldProbe,homeDirectory:URL(fileURLWithPath:"/fixture"))
  if case .held = try await heldService.prepare(prepare) { checks["actual actor preserves structured hold"] = true }
  let heldDirectory=await heldProbe.packetDirectory!
  checks["cleanup held private packet"] = !FileManager.default.fileExists(atPath:heldDirectory)
  let invalid=Resolver.PrepareRequest(caseID:caseID+"\n",windowID:window,workspaceID:workspace,expectedRevision:7)
  let untouched=Probe(response:response),invalidService=Resolver(commands:untouched,homeDirectory:URL(fileURLWithPath:"/fixture"))
  do { _ = try await invalidService.prepare(invalid) } catch {}
  checks["invalid request does not invoke process"] = await untouched.calls == 0
  let blocked=Probe(mode:"blocked",response:response),single=Resolver(commands:blocked,homeDirectory:URL(fileURLWithPath:"/fixture"))
  let first=Task { try await single.prepare(prepare) }
  for _ in 0..<400 { if await blocked.started { break };try await Task.sleep(nanoseconds:5_000_000) }
  var busy=false;do { _ = try await single.prepare(prepare) } catch Resolver.Failure.busy { busy=true } catch {}
  first.cancel();await blocked.release()
  var cancelled=false;do { _ = try await first.value } catch is CancellationError { cancelled=true } catch {}
  let blockedDirectory=await blocked.packetDirectory!
  let blockedCalls=await blocked.calls
  checks["singleflight rejects concurrent process"] = busy && blockedCalls == 1
  checks["cancelled return never exposes locator"] = cancelled
  checks["cancellation cleans private packet"] = !FileManager.default.fileExists(atPath:blockedDirectory)
  print(String(decoding:try JSONSerialization.data(withJSONObject:checks,options:.sortedKeys),as:UTF8.self))
 }
}
'''


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=120)


def main():
    with tempfile.TemporaryDirectory(prefix='case-source-resolver-contract-') as directory:
        p = Path(directory)
        sdk = ROOT/'Packages/macOS/CmuxExtensionKit/Sources/CmuxExtensionKit/Sidebar'
        run('xcrun', 'swiftc', '-swift-version', '6', '-emit-library', '-emit-module',
            '-module-name', 'CmuxExtensionKit', '-emit-module-path', str(p/'CmuxExtensionKit.swiftmodule'),
            '-o', str(p/'libCmuxExtensionKit.dylib'), str(sdk/'CmuxSidebarSourceMetadata.swift'))
        foundation = ROOT/'Packages/macOS/CmuxFoundation/Sources/CmuxFoundation/Process'
        (p/'Runner.swift').write_text('import Foundation\npublic struct CommandRunner: CommandRunning { public init(maximumCaptureBytes:Int?=nil) {}\npublic func run(directory:String,executable:String,arguments:[String],timeout:TimeInterval?) async -> CommandResult { fatalError("controlled command fixture must be injected") }}\n')
        run('xcrun', 'swiftc', '-swift-version', '6', '-emit-library', '-emit-module',
            '-module-name', 'CmuxFoundation', '-emit-module-path', str(p/'CmuxFoundation.swiftmodule'),
            '-o', str(p/'libCmuxFoundation.dylib'), str(foundation/'CommandRunning.swift'),
            str(foundation/'CommandResult.swift'), str(p/'Runner.swift'))
        (p/'Contract.swift').write_text(HARNESS)
        run('xcrun', 'swiftc', '-swift-version', '6', '-I', str(p), '-L', str(p),
            '-lCmuxExtensionKit', '-lCmuxFoundation', '-Xlinker', '-rpath', '-Xlinker', str(p),
            str(ROOT/'Sources/SidebarCaseSourceResolver.swift'), str(p/'Contract.swift'), '-o', str(p/'contract'))
        checks = json.loads(run(str(p/'contract')).stdout)
        for name, passed in checks.items():
            print(('PASS ' if passed else 'FAIL ')+name)
        print(json.dumps({'passed': sum(checks.values()), 'failed': sum(not x for x in checks.values()),
            'app_composition_verified': False, 'provider_verified': False, 'browser_opened': False}))
        return 0 if len(checks) >= 50 and all(checks.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
