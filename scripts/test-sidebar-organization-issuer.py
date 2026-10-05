#!/usr/bin/env python3
"""Exercise actual native issuer/adapter/core/ledger without starting Ghostty.

Only the app containers, session observation collector and command transport
are fixtures. The real context model, SDK types and transaction code execute.
This is not application, provider or runtime grant proof.
"""
from pathlib import Path
import json
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''import Foundation
import CmuxExtensionKit
import CmuxFoundation
@MainActor final class Workspace {
 enum Importance:String {case normal}
 let id=UUID();var title="Target";var isPinned=false;var importance=Importance.normal
 var isMuted=false;var customColor:String?;var groupId:UUID?;var groupPlacement:SidebarOrganizationPlacement?
 let workspaceContext=WorkspaceContextModel()
 var surfaces=[UUID()]
 var sessions:[SidebarOrganizationInput.Session]=[.init(toolId:"codex",sessionId:"native-session",directory:"/registered",title:"",context:nil,surfaceId:UUID().uuidString,processGeneration:1)]
 func sidebarOrderedPanelIds()->[UUID]{surfaces}
}
@MainActor struct Group {
 let id:UUID;var name:String;var customColor:String?;var iconSymbol:String?
 var isPinned=false;var isCollapsed=false;var externalID:String?;var liveAnchorWorkspaceId:UUID?;var isGeneratedAnchor=false
}
@MainActor final class TabManager {
 var windowId:UUID?=UUID();var tabs:[Workspace]=[];var workspaceGroups:[Group]=[]
 var selectedTabId:UUID?;var sidebarSelectedWorkspaceIds:Set<UUID>=[]
 var writes=0;var createdWithExistingChildren=false;var failAfterCreate=false;var inventoryUnavailable=false
 enum Reorder {case success,failure}
 func createWorkspaceGroup(name:String,childWorkspaceIds:[UUID],selectAnchor:Bool,collapseSidebarSelection:Bool,externalID:String)->UUID? {
  writes+=1;createdWithExistingChildren = !selectAnchor && !collapseSidebarSelection && !childWorkspaceIds.isEmpty
  let id=UUID();workspaceGroups.append(.init(id:id,name:name,externalID:externalID,liveAnchorWorkspaceId:childWorkspaceIds.first))
  for w in tabs where childWorkspaceIds.contains(w.id){w.groupId=id;w.groupPlacement=nil}
  if failAfterCreate{inventoryUnavailable=true}
  return id
 }
 func addWorkspaceToGroup(workspaceId:UUID,groupId:UUID){writes+=1;let w=tabs.first{$0.id==workspaceId}!;w.groupId=groupId;w.groupPlacement=nil}
 func removeWorkspaceFromGroup(workspaceId:UUID){writes+=1;let w=tabs.first{$0.id==workspaceId}!;w.groupId=nil;w.groupPlacement=nil}
 func ungroupWorkspaceGroup(groupId:UUID,removeGeneratedAnchor:Bool)->Bool{writes+=1;workspaceGroups.removeAll{$0.id==groupId};for w in tabs where w.groupId==groupId{w.groupId=nil;w.groupPlacement=nil};return !removeGeneratedAnchor}
 func reorderWorkspaces(orderedWorkspaceIds:[UUID])->Reorder {writes+=1;tabs=orderedWorkspaceIds.compactMap{id in tabs.first{$0.id==id}};return .success}
}
@MainActor enum SidebarWorkspaceRenderItem {
 static func effectiveGroupIdByWorkspaceId(tabs:[Workspace],groupsById:[UUID:Group])->[UUID:UUID?] {
  Dictionary(uniqueKeysWithValues:tabs.map{($0.id,$0.groupId)})
 }
}
@MainActor struct SidebarOrganizationInventoryBuilder {
 func make(tabManager:TabManager)->SidebarOrganizationInput {
  // A genuine unavailable observation is not represented by fabricated rows.
  if tabManager.inventoryUnavailable{return .init(id:UUID(),windowID:nil,createdAt:Date(),workspaces:[])}
  return .init(id:UUID(),windowID:tabManager.windowId,createdAt:Date(),workspaces:tabManager.tabs.map {w in
   .init(id:w.id.uuidString,title:w.title,revision:w.workspaceContext.context.revision,groupId:w.groupId?.uuidString,
    tags:w.workspaceContext.context.tags,aliases:[],summary:nil,rejectedAutomaticTagIDs:[],rejectedSourceFingerprints:[],sessions:w.sessions)
  })
 }
}
@MainActor final class Authorization {var allowed=true}
actor Probe:CommandRunning {
 var hash=String(repeating:"a",count:64)
 var hook:(@Sendable () async -> Void)?
 func setHash(_ value:String){hash=value}
 func setHook(_ value:@escaping @Sendable () async -> Void){hook=value}
 func run(directory:String,executable:String,arguments:[String],timeout:TimeInterval?) async->CommandResult {
  if arguments.first != "-c" {if let action=hook{hook=nil;await action()}}
  let data=try! JSONSerialization.data(withJSONObject:["schemaVersion":1,"authority":"none","registryFingerprint":hash])
  return .init(stdout:arguments.first == "-c" ? nil : String(decoding:data,as:UTF8.self),stderr:nil,exitStatus:0,timedOut:false,executionError:nil)
 }
}
@main struct Contract {
 @MainActor static func main() async throws {
  typealias P=SidebarOrganizationPlan
  var checks:[String:Bool]=[:]
  func rejects(_ action:() async throws -> Void) async -> Bool {do{try await action();return false}catch{return true}}
  func fixture(grouped:Bool=false,semantic:Bool=false) throws -> (TabManager,Workspace,Probe,SidebarOrganizationPlanIssuer) {
   let m=TabManager(),w=Workspace(),p=Probe();m.tabs=[w];m.selectedTabId=w.id;m.sidebarSelectedWorkspaceIds=[w.id]
   w.isPinned=true
   let manual=CmuxSidebarContextTag(id:"topic:hr",label:"HR",dimension:"topic",origin:.manual,source:"user")
   try w.workspaceContext.mutate(expectedRevision:0,mutation:.setManualTag(manual))
   let tag=CmuxSidebarContextTag(id:"repository:registered",label:"Registered",dimension:"repository",origin:.automatic,source:semantic ? "semantic-review" : "repo-classification:registered")
   let row=SidebarOrganizationOutput.Proposal(workspaceId:w.id.uuidString,expectedRevision:1,id:UUID(),suggestedTags:[tag],suggestedTitle:nil,summary:nil,source:"session-organization",sourceFingerprint:String(repeating:"b",count:64),conversationIDs:["native-session"],analyzedAt:Date(),evidence:[.init(kind:"registered-repository-directory",reference:"registered",sessionId:"native-session")])
   try w.workspaceContext.storeProposal(expectedRevision:1,proposal:row.contextProposal)
   if grouped{let g=UUID();m.workspaceGroups=[.init(id:g,name:"Manual HR",liveAnchorWorkspaceId:w.id)];w.groupId=g}
   let reader=SidebarOrganizationRegistryReader(engineURL:URL(fileURLWithPath:"/immutable/engine.py"),rulesURL:URL(fileURLWithPath:"/current/rules.yaml"),commands:p,pythonCandidates:["/python"])
   let issuer=SidebarOrganizationPlanIssuer(registry:reader)
   let inventory=try SidebarOrganizationNativeAdapter(manager:m,registryFingerprint:String(repeating:"a",count:64)).inventory()
   issuer.retain(output:.init(schemaVersion:1,proposals:[row],diagnostics:[],registryFingerprint:String(repeating:"a",count:64)),input:SidebarOrganizationInventoryBuilder().make(tabManager:m),inventory:inventory)
   return(m,w,p,issuer)
  }
  let(m,w,probe,issuer)=try fixture();let before=w.workspaceContext.context;let surfaces=w.surfaces
  let plan=try await issuer.prepare(manager:m,authorized:{true})
  checks["issued plan carries exact registered mapping"] = plan.authority == "none" && plan.assignments.count == 1 && plan.assignments[0].evidence == .registeredRepository
  let receipt=try await issuer.apply(plan.id,manager:m,authorized:{true});let writes=m.writes
  checks["uses existing children without terminal anchor"] = m.createdWithExistingChildren && m.tabs.count == 1 && w.surfaces == surfaces && m.workspaceGroups.first?.isGeneratedAnchor == false
  checks["preserves manual context pin and selection"] = w.workspaceContext.context == before && w.isPinned && m.selectedTabId == w.id && m.sidebarSelectedWorkspaceIds == [w.id]
  checks["automatic provenance belongs to actual issued plan"] = w.groupPlacement?.planID == plan.id && w.groupPlacement?.origin == .automatic
  let repeated=try await issuer.apply(plan.id,manager:m,authorized:{true})
  checks["same successful apply is idempotent"] = repeated == receipt && m.writes == writes
  try issuer.rollback(plan.id,manager:m,authorized:{true})
  checks["verified rollback restores only issued membership"] = w.groupId == nil && w.groupPlacement == nil && m.workspaceGroups.isEmpty && w.workspaceContext.context == before
  checks["consumed receipt cannot rollback twice"] = await rejects{try issuer.rollback(plan.id,manager:m,authorized:{true})}
  checks["caller invented identity grants no operation"] = await rejects{_ = try await issuer.apply(UUID(),manager:m,authorized:{true})}
  let(manual,_,_,manualIssuer)=try fixture(grouped:true)
  checks["legacy manual folder never becomes a candidate"] = await rejects{_ = try await manualIssuer.prepare(manager:manual,authorized:{true})} && manual.writes == 0
  let(semantic,_,_,semanticIssuer)=try fixture(semantic:true)
  checks["caller semantic label cannot invent registered placement"] = await rejects{_ = try await semanticIssuer.prepare(manager:semantic,authorized:{true})} && semantic.writes == 0
  let(drift,_,driftProbe,driftIssuer)=try fixture();let stalePlan=try await driftIssuer.prepare(manager:drift,authorized:{true})
  await driftProbe.setHash(String(repeating:"c",count:64))
  checks["registry drift holds before native write"] = await rejects{_ = try await driftIssuer.apply(stalePlan.id,manager:drift,authorized:{true})} && drift.writes == 0
  for mode in ["directory","session","generation","surface"] {
   let(native,row,_,service)=try fixture();let retained=try await service.prepare(manager:native,authorized:{true})
   if mode == "surface"{row.surfaces.append(UUID())}
   else {let original=row.sessions[0];row.sessions[0] = .init(toolId:original.toolId,sessionId:mode == "session" ? "replacement" : original.sessionId,directory:mode == "directory" ? "/other" : original.directory,title:original.title,context:nil,surfaceId:original.surfaceId,processGeneration:mode == "generation" ? 2 : 1)}
   checks[mode+" drift holds without context revision change"] = await rejects{_ = try await service.apply(retained.id,manager:native,authorized:{true})} && native.writes == 0
  }
  let(revoked,_,revokeProbe,revokeIssuer)=try fixture();let revokePlan=try await revokeIssuer.prepare(manager:revoked,authorized:{true});let authorization=Authorization()
  await revokeProbe.setHook {await MainActor.run{authorization.allowed=false}}
  checks["revocation during provider wait holds before writes"] = await rejects{_ = try await revokeIssuer.apply(revokePlan.id,manager:revoked,authorized:{authorization.allowed})} && revoked.writes == 0
  let(same,row,_,sameIssuer)=try fixture();let samePlan=try await sameIssuer.prepare(manager:same,authorized:{true});_ = try await sameIssuer.apply(samePlan.id,manager:same,authorized:{true});row.groupPlacement=nil;let sameWrites=same.writes
  checks["same-group manual provenance prevents rollback"] = await rejects{try sameIssuer.rollback(samePlan.id,manager:same,authorized:{true})} && same.writes == sameWrites && row.groupId != nil
  let(noRegistry,_,_,_)=try fixture();let unavailable=SidebarOrganizationPlanIssuer(registry:nil)
  checks["missing signed engine configuration grants no plan"] = await rejects{_ = try await unavailable.prepare(manager:noRegistry,authorized:{true})} && noRegistry.writes == 0
  let(partial,_,_,partialIssuer)=try fixture();let partialPlan=try await partialIssuer.prepare(manager:partial,authorized:{true});partial.failAfterCreate=true
  var diagnosed=false
  do {_ = try await partialIssuer.apply(partialPlan.id,manager:partial,authorized:{true})}
  catch let error as SidebarOrganizationPlanCoordinator.RecoveryRequired {
   diagnosed = error.planID == partialPlan.id && error.before == partialPlan.before && error.observed != nil
  }
  let partialWrites=partial.writes
  checks["post-write compensation failure retains actual core diagnosis"] = diagnosed && partial.tabs[0].groupId != nil
  checks["partial outcome grants no automatic retry"] = await rejects{_ = try await partialIssuer.apply(partialPlan.id,manager:partial,authorized:{true})} && partial.writes == partialWrites
  _ = probe
  print(String(decoding:try JSONSerialization.data(withJSONObject:checks,options:.sortedKeys),as:UTF8.self))
 }
}
'''


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=120)


def main():
    with tempfile.TemporaryDirectory(prefix='native-issuer-contract-') as directory:
        p = Path(directory)
        sdk = ROOT/'Packages/macOS/CmuxExtensionKit/Sources/CmuxExtensionKit/Sidebar'
        names = ['CmuxSidebarContextTag.swift', 'CmuxSidebarContextTagOrigin.swift', 'CmuxSidebarWorkspaceContextMutation.swift', 'CmuxSidebarWorkspaceContextProposal.swift', 'CmuxSidebarWorkspaceContext.swift', 'CmuxSidebarSourceMetadata.swift']
        run('xcrun', 'swiftc', '-swift-version', '6', '-emit-library', '-emit-module', '-module-name', 'CmuxExtensionKit', '-emit-module-path', str(p/'CmuxExtensionKit.swiftmodule'), '-o', str(p/'libCmuxExtensionKit.dylib'), *[str(sdk/n) for n in names])
        scrub = ROOT/'Packages/Shared/CmuxSentryTelemetry/Sources/CmuxSentryScrubbing'
        run('xcrun', 'swiftc', '-swift-version', '6', '-emit-library', '-emit-module', '-module-name', 'CmuxSentryScrubbing', '-emit-module-path', str(p/'CmuxSentryScrubbing.swiftmodule'), '-o', str(p/'libCmuxSentryScrubbing.dylib'), *map(str, scrub.glob('*.swift')))
        foundation = ROOT/'Packages/macOS/CmuxFoundation/Sources/CmuxFoundation/Process'
        (p/'Runner.swift').write_text('import Foundation\npublic struct CommandRunner:CommandRunning {public init(maximumCaptureBytes:Int?=nil){}\npublic func run(directory:String,executable:String,arguments:[String],timeout:TimeInterval?) async->CommandResult{fatalError("test injects protocol seam")}}\n')
        run('xcrun', 'swiftc', '-swift-version', '6', '-emit-library', '-emit-module', '-module-name', 'CmuxFoundation', '-emit-module-path', str(p/'CmuxFoundation.swiftmodule'), '-o', str(p/'libCmuxFoundation.dylib'), str(foundation/'CommandRunning.swift'), str(foundation/'CommandResult.swift'), str(p/'Runner.swift'))
        (p/'Contract.swift').write_text(HARNESS)
        files = ['WorkspaceContextModel.swift', 'SidebarOrganizationInput.swift', 'SidebarOrganizationOutput.swift', 'SidebarOrganizationPlan.swift', 'SidebarOrganizationPlanCoordinator.swift', 'SidebarOrganizationPlacement.swift', 'SidebarOrganizationPlacementLedger.swift', 'SidebarOrganizationNativeAdapter.swift', 'SidebarOrganizationRegistryReader.swift', 'SidebarOrganizationPlanIssuer.swift']
        run('xcrun', 'swiftc', '-swift-version', '6', '-I', str(p), '-L', str(p), '-lCmuxExtensionKit', '-lCmuxSentryScrubbing', '-lCmuxFoundation', '-Xlinker', '-rpath', '-Xlinker', str(p), *[str(ROOT/'Sources'/n) for n in files], str(p/'Contract.swift'), '-o', str(p/'contract'))
        checks = json.loads(run(str(p/'contract')).stdout)
        for name, passed in checks.items():
            print(('PASS ' if passed else 'FAIL ')+name)
        print(json.dumps({'passed': sum(checks.values()), 'failed': sum(not value for value in checks.values())}))
        return 0 if len(checks) == 20 and all(checks.values()) else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        print(error.stderr)
        raise SystemExit(2)
