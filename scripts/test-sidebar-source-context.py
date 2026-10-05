#!/usr/bin/env python3
"""Execute actual persisted source context and revision mutations; no app launch."""
from pathlib import Path
import json,subprocess,tempfile
ROOT=Path(__file__).resolve().parents[1]
SDK=ROOT/'Packages/macOS/CmuxExtensionKit/Sources/CmuxExtensionKit/Sidebar'
HARNESS=r'''import Foundation
import CmuxExtensionKit
@main struct Contract {
 @MainActor static func main() throws {
  let model=WorkspaceContextModel()
  let tag=CmuxSidebarContextTag(id:"topic:hr",label:"HR",dimension:"topic",origin:.manual,source:"user")
  try model.mutate(expectedRevision:0,mutation:.setManualTag(tag))
  let ref=CmuxSidebarSourceReference(accountRef:"MBX-"+String(repeating:"a",count:64),directoryUserId:"123456789",resourceId:"GTK-"+String(repeating:"b",count:64),messageId:"message123",evidenceFingerprint:String(repeating:"c",count:64))
  let request="CEO-"+String(repeating:"a",count:32)
  var r:[String:Bool]=[:]
  let revision=try model.attachSourceReference(expectedRevision:1,reference:ref,requestID:request)
  r["nativePostAttachmentRevision"] = revision == 2 && model.context.sourceRequestBindings?.first?.attachedRevision == revision
  r["manualTagPreserved"] = model.context.tags == [tag]
  r["bindingMatchesStrictFingerprint"] = model.context.sourceRequestBindings?.first?.sourceReferenceFingerprint == ref.fingerprint
  let persisted=model.persisted
  r["duplicateIsIdempotent"] = try model.attachSourceReference(expectedRevision:revision,reference:ref,requestID:request) == revision && model.persisted == persisted
  func fails(_ action:() throws -> Void)->Bool { do {try action();return false} catch{return true} }
  r["staleProviderReadCannotAttach"] = fails { _ = try model.attachSourceReference(expectedRevision:1,reference:ref,requestID:request) } && model.persisted == persisted
  r["unissuedRequestShapeRejected"] = fails { _ = try model.attachSourceReference(expectedRevision:revision,reference:ref,requestID:"CEO-invented") } && model.persisted == persisted
  r["conflictingRequestRejected"] = fails { _ = try model.attachSourceReference(expectedRevision:revision,reference:ref,requestID:"CEO-"+String(repeating:"b",count:32)) } && model.persisted == persisted
  let encoder=JSONEncoder();let decoder=JSONDecoder()
  let restored=try decoder.decode(WorkspaceContextModel.Persisted.self,from:encoder.encode(persisted))
  r["persistenceRoundTrip"] = restored == persisted
  var object=try JSONSerialization.jsonObject(with:encoder.encode(model.context)) as! [String:Any]
  object.removeValue(forKey:"sourceReferences");object.removeValue(forKey:"sourceRequestBindings")
  let old=try decoder.decode(CmuxSidebarWorkspaceContext.self,from:JSONSerialization.data(withJSONObject:object))
  r["oldContextCompatible"] = old.sourceReferences == nil && old.sourceRequestBindings == nil
  var bindings=object;bindings["sourceReferences"] = [try JSONSerialization.jsonObject(with:encoder.encode(ref))]
  bindings["sourceRequestBindings"] = [["requestId":request,"sourceReferenceFingerprint":ref.fingerprint!,"attachedRevision":99]]
  r["futureAttachmentRevisionRejected"] = fails { _ = try decoder.decode(CmuxSidebarWorkspaceContext.self,from:JSONSerialization.data(withJSONObject:bindings)) }
  let previous=try model.pendingUndo(expectedRevision:revision);model.commitUndo(previous,revision:revision)
  r["undoRemovesOnlyAttachment"] = model.context.tags == [tag] && model.context.sourceReferences == nil && model.context.sourceRequestBindings == nil && model.context.revision == 3
  let bounded=WorkspaceContextModel()
  for i in 0..<32 {
   let unique=String(format:"%064x",i)
   let item=CmuxSidebarSourceReference(accountRef:ref.accountRef,directoryUserId:ref.directoryUserId,resourceId:"GTK-"+unique,messageId:"message",evidenceFingerprint:unique)
   _ = try bounded.attachSourceReference(expectedRevision:UInt64(i),reference:item,requestID:"CEO-"+String(format:"%032x",i))
  }
  let full=bounded.persisted
  r["nativeBound32"] = fails { _ = try bounded.attachSourceReference(expectedRevision:32,reference:ref,requestID:request) } && bounded.persisted == full
  print(String(decoding:try JSONSerialization.data(withJSONObject:r,options:[.sortedKeys]),as:UTF8.self))
 }
}
'''
def main():
 with tempfile.TemporaryDirectory(prefix='native-source-context-') as temp:
  root=Path(temp);files=['CmuxSidebarContextTag.swift','CmuxSidebarContextTagOrigin.swift','CmuxSidebarWorkspaceContextMutation.swift','CmuxSidebarWorkspaceContextProposal.swift','CmuxSidebarWorkspaceContext.swift','CmuxSidebarSourceMetadata.swift']
  subprocess.run(['xcrun','swiftc','-swift-version','6','-emit-library','-emit-module','-module-name','CmuxExtensionKit','-emit-module-path',str(root/'CmuxExtensionKit.swiftmodule'),'-o',str(root/'libCmuxExtensionKit.dylib'),*[str(SDK/f) for f in files]],check=True,capture_output=True,text=True,timeout=120)
  (root/'Contract.swift').write_text(HARNESS);binary=root/'contract'
  subprocess.run(['xcrun','swiftc','-swift-version','6','-I',str(root),'-L',str(root),'-lCmuxExtensionKit','-Xlinker','-rpath','-Xlinker',str(root),str(ROOT/'Sources/WorkspaceContextModel.swift'),str(root/'Contract.swift'),'-o',str(binary)],check=True,capture_output=True,text=True,timeout=120)
  values=json.loads(subprocess.run([str(binary)],check=True,capture_output=True,text=True,timeout=15).stdout)
  for name,ok in values.items():print(('PASS ' if ok else 'FAIL ')+name)
  print(json.dumps({'passed':sum(values.values()),'failed':sum(not v for v in values.values())}))
  return 0 if len(values)==12 and all(values.values()) else 1
if __name__=='__main__':raise SystemExit(main())
