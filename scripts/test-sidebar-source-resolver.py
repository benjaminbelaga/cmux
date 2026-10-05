#!/usr/bin/env python3
"""Run actual Swift resolver on retained-scope response fixtures; no app or mail."""
from pathlib import Path
import json, subprocess, tempfile
ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''import Foundation
import CmuxExtensionKit
@main struct Contract {
 static func main() throws {
  let id=UUID(); let requestID="CEO-"+String(repeating:"a",count:32)
  let ref=CmuxSidebarSourceReference(accountRef:"MBX-"+String(repeating:"a",count:64),directoryUserId:"123",resourceId:"GTK-"+String(repeating:"b",count:64),messageId:"message123",evidenceFingerprint:String(repeating:"c",count:64))
  let request=SidebarSourceReferenceResolver.Request(requestID:requestID,workspaceID:id,expectedRevision:7,sourceReference:ref)
  let now=Date(timeIntervalSince1970:1791240000)
  let formatter=ISO8601DateFormatter()
  let locator:[String:Any] = ["provider":"gmail","principal":"ben@yoyaku.fr","workspace_id":"yoyaku.fr","directory_user_id":"123","account_ref":ref.accountRef,"gmail_thread_key":ref.resourceId,"gmail_thread_id":"thread","gmail_message_id":"message123","url":"https://mail.google.com/mail/?authuser=ben%40yoyaku.fr#all/thread"]
  let good:[String:Any] = ["status":"verified","verified":true,"request_id":requestID,"workspace_id":id.uuidString,"expected_revision":7,"observed_at":formatter.string(from:now),"source_reference":try JSONSerialization.jsonObject(with:JSONEncoder().encode(ref)),"locator":locator,"source_provenance":"Canonical retained request, immutable Directory UID and fresh authenticated provider content.","business_authority":"none","native_provider_session_binding":false,"model_calls":0,"sends":0,"drafts":0,"next_action":"Native caller must check current state."]
  var checks:[String:Bool]=[:]
  let ContractRequest=request
  func accepted(_ object:[String:Any], request:SidebarSourceReferenceResolver.Request?=nil)->Bool {
   do {let data=try JSONSerialization.data(withJSONObject:object);_ = try SidebarSourceReferenceResolver.validate(data,request:request ?? ContractRequest,now:now);return true} catch{return false}
  }
  checks["exact fresh authenticated locator accepted"] = accepted(good)
  for (name,value) in [("request_id","CEO-"+String(repeating:"b",count:32)),("workspace_id",UUID().uuidString),("business_authority","admin")] {
   var changed=good;changed[name]=value;checks["reject changed "+name] = !accepted(changed)
  }
  for (name,value) in [("expected_revision",8),("model_calls",1),("sends",1),("drafts",1)] {
   var changed=good;changed[name]=value;checks["reject changed "+name] = !accepted(changed)
  }
  for (name,value) in [("url","https://mail.google.com/mail/?authuser=other%40yoyaku.fr#all/thread"),("principal","ben@yoyaku.fr/evil"),("directory_user_id","999"),("account_ref","MBX-foreign"),("gmail_thread_key","GTK-foreign"),("gmail_message_id","foreign"),("gmail_thread_id","thread/another"),("provider","other")] {
   var changed=good;var nested=locator;nested[name]=value;changed["locator"]=nested;checks["reject locator "+name] = !accepted(changed)
  }
  for seconds in [-121.0,6.0] {
   var changed=good;changed["observed_at"]=formatter.string(from:now.addingTimeInterval(seconds));checks["reject stale or future "+String(seconds)] = !accepted(changed)
  }
  var extra=good;extra["override_url"]="https://example.com";checks["reject extra response authority"] = !accepted(extra)
  var bool=good;bool["model_calls"]=true;checks["reject boolean counter"] = !accepted(bool)
  var substitution=good;var other=ref;other.evidenceFingerprint=String(repeating:"d",count:64);substitution["source_reference"]=try JSONSerialization.jsonObject(with:JSONEncoder().encode(other));checks["reject source content drift"] = !accepted(substitution)
  let invalid=SidebarSourceReferenceResolver.Request(requestID:"CEO-invalid",workspaceID:id,expectedRevision:7,sourceReference:ref)
  checks["reject malformed request"] = !accepted(good,request:invalid)
  var hold=good;hold["verified"]=false;hold["status"]="source_transport_hold";checks["hold never provides a locator"] = !accepted(hold)
  let data=try JSONSerialization.data(withJSONObject:checks,options:.sortedKeys)
  print(String(decoding:data,as:UTF8.self))
 }
}
'''
def run(*args):
    return subprocess.run(args,check=True,capture_output=True,text=True,timeout=120)
def main():
    with tempfile.TemporaryDirectory(prefix='source-resolver-contract-') as temp:
        p=Path(temp);sdk=ROOT/'Packages/macOS/CmuxExtensionKit/Sources/CmuxExtensionKit/Sidebar'
        run('xcrun','swiftc','-swift-version','6','-emit-library','-emit-module','-module-name','CmuxExtensionKit','-emit-module-path',str(p/'CmuxExtensionKit.swiftmodule'),'-o',str(p/'libCmuxExtensionKit.dylib'),str(sdk/'CmuxSidebarSourceMetadata.swift'))
        foundation=ROOT/'Packages/macOS/CmuxFoundation/Sources/CmuxFoundation/Process'
        (p/'Runner.swift').write_text('import Foundation\npublic struct CommandRunner: CommandRunning { public init(maximumCaptureBytes:Int?=nil) {}\npublic func run(directory:String,executable:String,arguments:[String],timeout:TimeInterval?) async -> CommandResult { fatalError("never called by parser fixture") }}\n')
        run('xcrun','swiftc','-swift-version','6','-emit-library','-emit-module','-module-name','CmuxFoundation','-emit-module-path',str(p/'CmuxFoundation.swiftmodule'),'-o',str(p/'libCmuxFoundation.dylib'),str(foundation/'CommandRunning.swift'),str(foundation/'CommandResult.swift'),str(p/'Runner.swift'))
        (p/'Contract.swift').write_text(HARNESS)
        run('xcrun','swiftc','-swift-version','6','-I',str(p),'-L',str(p),'-lCmuxExtensionKit','-lCmuxFoundation','-Xlinker','-rpath','-Xlinker',str(p),str(ROOT/'Sources/SidebarSourceReferenceResolver.swift'),str(p/'Contract.swift'),'-o',str(p/'contract'))
        checks=json.loads(run(str(p/'contract')).stdout)
        for name,passed in checks.items():print(('PASS ' if passed else 'FAIL ')+name)
        print(json.dumps({'passed':sum(checks.values()),'failed':sum(not x for x in checks.values())}))
        return 0 if len(checks)==23 and all(checks.values()) else 1
if __name__=='__main__':raise SystemExit(main())
