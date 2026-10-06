#!/usr/bin/env python3
"""Execute actual organization/source RPC callers across accepted-connection revocation.

Wire DTOs, coordinators, context model and caller extension are production code.
Only native containers, provider transport and listener-generation observation
are fixtures. This is source behavior, not an app or authenticated provider proof.
"""
from pathlib import Path
import importlib.util
import json
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('coordinator_fixture',ROOT/'scripts/test-sidebar-organization-coordinator.py')
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
containers=fixture.HARNESS.split('@main struct Contract {')[0]
containers=containers.replace('var selectedTabId:UUID?', 'var sidebarSourceReferenceCoordinator:SidebarSourceReferenceCoordinator!\n var sidebarOrganizationCoordinator:SidebarOrganizationCoordinator!\n var selectedTabId:UUID?')
containers=containers.replace('id:"topic:hr",label:"HR",dimension:"topic",origin:.automatic,source:"session-organization"','id:"repository:registered",label:"Registered",dimension:"repository",origin:.automatic,source:"repo-classification:registered"')
containers=containers.replace('analyzedAt:Date(),evidence:nil','analyzedAt:Date(),evidence:[.init(kind:"registered-repository-directory",reference:"registered",sessionId:w.sessions[0].sessionId)]')
containers=containers.replace('proposals:rows,diagnostics:[])','proposals:rows,diagnostics:[],registryFingerprint:String(repeating:"a",count:64))')
containers=containers.replace('var trim=false;var hook:', 'var prepareHook:(@Sendable () async -> Void)?\n func setPrepareHook(_ value:@escaping @Sendable () async -> Void){prepareHook=value}\n var trim=false;var hook:')
containers=containers.replace('var result=input\n', 'if let prepareHook{self.prepareHook=nil;await prepareHook()}\n  var result=input\n')
HARNESS=containers+r'''
import CmuxControlSocket
@MainActor final class AppDelegate {
 static var shared:AppDelegate?;let manager:TabManager
 init(_ manager:TabManager){self.manager=manager}
 func tabManagerFor(windowId:UUID)->TabManager?{manager.windowId == windowId ? manager : nil}
 func tabManagerFor(tabId:UUID)->TabManager?{manager.tabs.contains{$0.id==tabId} ? manager : nil}
}
@MainActor final class TerminalController {
 let manager:TabManager;init(_ manager:TabManager){self.manager=manager}
 func v2ResolveTabManager(params:[String:Any])->TabManager?{manager}
 func v2Ok(id:Any?,result:Any)->String{encode(["ok":true,"result":result])}
 func v2Error(id:Any?,code:String,message:String,data:[String:Any]?=nil)->String{encode(["ok":false,"code":code])}
 private func encode(_ value:[String:Any])->String{String(decoding:try! JSONSerialization.data(withJSONObject:value),as:UTF8.self)}
}
final class Generation:@unchecked Sendable {
 private let lock=NSLock();private var generation=0
 func rotate(){lock.lock();defer{lock.unlock()};generation+=1}
 func isCurrent(_ expected:Int)->Bool{lock.lock();defer{lock.unlock()};return generation==expected}
}
@main struct Contract {
 @MainActor static func main() async throws {
  var checks:[String:Bool]=[:]
  let ref=CmuxSidebarSourceReference(accountRef:"MBX-"+String(repeating:"a",count:64),directoryUserId:"123",resourceId:"GTK-"+String(repeating:"b",count:64),messageId:"message",evidenceFingerprint:String(repeating:"c",count:64))
  let locator=SidebarSourceReferenceResolver.Locator(provider:"gmail",principal:"ben@yoyaku.fr",workspaceID:"yoyaku.fr",directoryUserID:"123",accountRef:ref.accountRef,gmailThreadKey:ref.resourceId,gmailThreadID:"thread",gmailMessageID:"message",url:"https://mail.google.com/mail/?authuser=ben%40yoyaku.fr#all/thread")
  func fixture()->(TabManager,Workspace,Probe,Analysis,TerminalController){
   let m=TabManager(),w=Workspace(),probe=Probe(),analysis=Analysis();m.tabs=[w];AppDelegate.shared=AppDelegate(m)
   let registry=SidebarOrganizationRegistryReader(engineURL:URL(fileURLWithPath:"/immutable/engine.py"),rulesURL:URL(fileURLWithPath:"/current/rules.yaml"),commands:probe,pythonCandidates:["/python"])
   m.sidebarOrganizationCoordinator=SidebarOrganizationCoordinator(service:analysis,registry:registry)
   m.sidebarSourceReferenceCoordinator=SidebarSourceReferenceCoordinator(resolveSource:{_ in locator},openURL:{_ in fatalError("attach never opens")})
   return(m,w,probe,analysis,TerminalController(m))
  }
  func request(_ method:String,_ params:[String:Any])->ControlRequest{.init(id:.int(1),method:method,params:params.mapValues{JSONValue(foundationObject:$0)!})}
  func accepted(_ response:String)->Bool{(try! JSONSerialization.jsonObject(with:Data(response.utf8)) as! [String:Any])["ok"] as? Bool == true}
  let refObject=try JSONSerialization.jsonObject(with:JSONEncoder().encode(ref))
  for revoked in [false,true] {
   let(m,w,_,_,controller)=fixture();let generation=Generation();let authority=SocketCommandAuthorization(isCurrent:{generation.isCurrent(0)})
   m.sidebarSourceReferenceCoordinator=SidebarSourceReferenceCoordinator(resolveSource:{_ in if revoked{generation.rotate()};return locator},openURL:{_ in fatalError("attach never opens")})
   let packet:[String:Any]=["window_id":m.windowId!.uuidString,"workspace_id":w.id.uuidString,"expected_revision":0,"request_id":"CEO-"+String(repeating:"a",count:32),"source_reference":refObject]
   let result=await SocketCommandAuthorization.$current.withValue(authority){await controller.workspaceOrganizationResponse(request("workspace.source.attach",packet))}
   checks[revoked ? "revoked connection cannot attach after provider await" : "current connection attaches verified source"] = revoked ? !accepted(result) && w.workspaceContext.context.revision==0 && w.workspaceContext.context.sourceReferences==nil : accepted(result) && w.workspaceContext.context.revision==1
  }
  for revoked in [false,true] {
   let(m,w,probe,_,controller)=fixture();let plan=try await m.sidebarOrganizationCoordinator.preparePlan(tabManager:m,authorized:{true});let before=w.workspaceContext.persisted;let generation=Generation();let authority=SocketCommandAuthorization(isCurrent:{generation.isCurrent(0)})
   if revoked{await probe.setHook{generation.rotate()}}
   let packet:[String:Any]=["window_id":m.windowId!.uuidString,"plan_id":plan.id.uuidString]
   let result=await SocketCommandAuthorization.$current.withValue(authority){await controller.workspaceOrganizationResponse(request("workspace.organization.apply",packet))}
   checks[revoked ? "revoked connection cannot apply after registry await" : "current connection applies native-issued plan"] = revoked ? !accepted(result) && m.writes==0 && w.workspaceContext.persisted==before : accepted(result) && m.writes>0 && w.groupPlacement?.planID==plan.id
   if !revoked {generation.rotate();let writes=m.writes;let undo=await SocketCommandAuthorization.$current.withValue(authority){await controller.workspaceOrganizationResponse(request("workspace.organization.rollback",packet))};checks["revoked connection cannot rollback before core writes"] = !accepted(undo) && m.writes==writes && w.groupId != nil}
  }
  let(m,w,_,_,controller)=fixture();let packet:[String:Any]=["window_id":m.windowId!.uuidString,"workspace_id":w.id.uuidString,"expected_revision":0,"request_id":"CEO-"+String(repeating:"a",count:32),"source_reference":refObject]
  let absent=await controller.workspaceOrganizationResponse(request("workspace.source.attach",packet))
  checks["missing accepted socket authority grants no attachment"] = !accepted(absent) && w.workspaceContext.context.revision==0
  let(exportManager,_,_,analysis,exportController)=fixture();let generation=Generation();let authority=SocketCommandAuthorization(isCurrent:{generation.isCurrent(0)})
  await analysis.setPrepareHook{generation.rotate()}
  let export=await SocketCommandAuthorization.$current.withValue(authority){await exportController.workspaceOrganizationResponse(request("workspace.context.export",["window_id":exportManager.windowId!.uuidString]))}
  checks["revoked export cannot retain or disclose prepared context"] = !accepted(export)
  print(String(decoding:try JSONSerialization.data(withJSONObject:checks,options:.sortedKeys),as:UTF8.self))
 }
}
'''


def run(*args):return subprocess.run(args,check=True,capture_output=True,text=True,timeout=120)

def main():
 with tempfile.TemporaryDirectory(prefix='native-socket-organization-') as temp:
  p=Path(temp);sdk=ROOT/'Packages/macOS/CmuxExtensionKit/Sources/CmuxExtensionKit/Sidebar'
  names=['CmuxSidebarContextTag.swift','CmuxSidebarContextTagOrigin.swift','CmuxSidebarWorkspaceContextMutation.swift','CmuxSidebarWorkspaceContextProposal.swift','CmuxSidebarWorkspaceContext.swift','CmuxSidebarSourceMetadata.swift','CMUXExtensionActionResult.swift']
  run('xcrun','swiftc','-swift-version','6','-emit-library','-emit-module','-module-name','CmuxExtensionKit','-emit-module-path',str(p/'CmuxExtensionKit.swiftmodule'),'-o',str(p/'libCmuxExtensionKit.dylib'),*[str(sdk/n) for n in names])
  wire=ROOT/'Packages/macOS/CmuxControlSocket/Sources/CmuxControlSocket/Wire'
  run('xcrun','swiftc','-swift-version','6','-emit-library','-emit-module','-module-name','CmuxControlSocket','-emit-module-path',str(p/'CmuxControlSocket.swiftmodule'),'-o',str(p/'libCmuxControlSocket.dylib'),str(wire/'JSONValue.swift'),str(wire/'ControlRequest.swift'))
  scrub=ROOT/'Packages/Shared/CmuxSentryTelemetry/Sources/CmuxSentryScrubbing'
  run('xcrun','swiftc','-swift-version','6','-emit-library','-emit-module','-module-name','CmuxSentryScrubbing','-emit-module-path',str(p/'CmuxSentryScrubbing.swiftmodule'),'-o',str(p/'libCmuxSentryScrubbing.dylib'),*map(str,scrub.glob('*.swift')))
  foundation=ROOT/'Packages/macOS/CmuxFoundation/Sources/CmuxFoundation/Process'
  (p/'Runner.swift').write_text('import Foundation\npublic struct CommandRunner:CommandRunning {public init(maximumCaptureBytes:Int?=nil){}\npublic func run(directory:String,executable:String,arguments:[String],timeout:TimeInterval?) async->CommandResult{fatalError("test injects protocol seam")}}\n')
  run('xcrun','swiftc','-swift-version','6','-emit-library','-emit-module','-module-name','CmuxFoundation','-emit-module-path',str(p/'CmuxFoundation.swiftmodule'),'-o',str(p/'libCmuxFoundation.dylib'),str(foundation/'CommandRunning.swift'),str(foundation/'CommandResult.swift'),str(p/'Runner.swift'))
  resolver=(ROOT/'Sources/SidebarSourceReferenceResolver.swift').read_text();shape=resolver[resolver.index('    enum Failure:'):resolver.index('    private struct Report:')]
  (p/'Resolver.swift').write_text('import Foundation\nimport CmuxExtensionKit\nactor SidebarSourceReferenceResolver {\n'+shape+'\nfunc resolve(_ request:Request) async throws -> Locator {throw Failure.held}\n}\n')
  authority=ROOT/'Sources/SocketCommandAuthorization.swift'
  if not authority.exists():
   # The failing native caller receives an accepted connection scope but does
   # not consume it. No substitute policy authorizes the native transaction.
   authority=p/'SocketCommandAuthorization.swift'
   authority.write_text('import Foundation\nstruct SocketCommandAuthorization:Sendable {\n@TaskLocal static var current:Self?\nlet isCurrent:@Sendable ()->Bool\nvar isValid:Bool{!Task.isCancelled && isCurrent()}\n}\n')
  (p/'Contract.swift').write_text(HARNESS)
  files=['WorkspaceContextModel.swift','SidebarOrganizationInput.swift','SidebarOrganizationOutput.swift','SidebarOrganizationPlan.swift','SidebarOrganizationPlanCoordinator.swift','SidebarOrganizationPlacement.swift','SidebarOrganizationPlacementLedger.swift','SidebarOrganizationNativeAdapter.swift','SidebarOrganizationRegistryReader.swift','SidebarOrganizationPlanIssuer.swift','SidebarOrganizationAnalyzing.swift','SidebarOrganizationInventoryBuilder.swift','SidebarOrganizationCoordinator.swift','SidebarSourceReferenceCoordinator.swift','TerminalController+WorkspaceOrganization.swift']
  run('xcrun','swiftc','-swift-version','6','-I',str(p),'-L',str(p),'-lCmuxExtensionKit','-lCmuxControlSocket','-lCmuxSentryScrubbing','-lCmuxFoundation','-Xlinker','-rpath','-Xlinker',str(p),*[str(ROOT/'Sources'/n) for n in files],str(p/'Resolver.swift'),str(authority),str(p/'Contract.swift'),'-o',str(p/'contract'))
  checks=json.loads(run(str(p/'contract')).stdout)
  for name,passed in checks.items():print(('PASS ' if passed else 'FAIL ')+name)
  print(json.dumps({'passed':sum(checks.values()),'failed':sum(not v for v in checks.values())}))
  return 0 if len(checks)==7 and all(checks.values()) else 1

if __name__=='__main__':
 try:raise SystemExit(main())
 except subprocess.CalledProcessError as error:print(error.stderr);raise SystemExit(2)
