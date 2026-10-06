#!/usr/bin/env python3
"""Execute actual classification coordinator and inventory collector drift fences.

Native containers/projector and classification transport are fixtures. Actual
inventory collection, SDK browser evidence, context model and coordinator run.
No app, provider or effective runtime grants are asserted by this leaf.
"""
from pathlib import Path
import importlib.util
import json
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('issuer_fixture', ROOT/'scripts/test-sidebar-organization-issuer.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
containers = fixture.HARNESS.split('@main struct Contract {')[0]
start = containers.index('@MainActor struct SidebarOrganizationInventoryBuilder')
end = containers.index('@MainActor final class Authorization', start)
containers = containers[:start] + containers[end:]
containers = containers.replace('import CmuxExtensionKit', '@_spi(CmuxHostTransport) import CmuxExtensionKit')
containers = containers.replace('var surfaces=[UUID()]', '''var surfaces=[UUID()]
 var browserURL:URL?
 struct Browser {let currentURL:URL?}
 func browserPanel(for panel:UUID)->Browser?{surfaces.contains(panel) && browserURL != nil ? Browser(currentURL:browserURL) : nil}
 func reportedPanelDirectory(panelId:UUID)->String?{sessions.first?.directory}
 func panelTitle(panelId:UUID)->String?{sessions.first?.title}''')
HARNESS = containers + r'''
@MainActor struct SidebarExtensionRuntimeProjector {
 struct Observation {let sessionID:String?;let toolID:String?;let processGeneration:UInt64?}
 func observations(workspace:Workspace,panelID:UUID)->[Observation]? {
  guard panelID == workspace.surfaces.first else{return nil}
  return workspace.sessions.map{.init(sessionID:$0.sessionId,toolID:$0.toolId,processGeneration:$0.processGeneration)}
 }
}
@MainActor final class Clock {var value=Date(timeIntervalSince1970:1_790_208_000);func next()->Date{defer{value+=1};return value}}
actor Analysis:SidebarOrganizationAnalyzing {
 var trim=false;var registered=false;var hook:(@Sendable () async -> Void)?
 func configure(trim:Bool=false,registered:Bool=false,hook:(@Sendable () async -> Void)?=nil){self.trim=trim;self.registered=registered;self.hook=hook}
 func prepare(_ input:SidebarOrganizationInput) async throws -> SidebarOrganizationInput {
  var result=input
  for index in result.workspaces.indices {
   result.workspaces[index].sessions[0].context = .init(recentMessages:[.init(role:"user",text:"bounded context")],contextStatus:.observed)
   if trim {result.workspaces[index].groupName=nil;result.workspaces[index].groupOrigin=nil;result.workspaces[index].sourceReferences=[];result.workspaces[index].serviceObservations=[]}
  }
  return result
 }
 func analyze(_ input:SidebarOrganizationInput,review:Data?) async throws -> SidebarOrganizationOutput {
  if let hook{self.hook=nil;await hook()}
  let rows=input.workspaces.map {w in
   SidebarOrganizationOutput.Proposal(workspaceId:w.id,expectedRevision:w.revision,id:UUID(),suggestedTags:[.init(id:registered ? "project:registered" : "topic:hr",label:registered ? "Registered" : "HR",dimension:registered ? "project" : "topic",origin:.automatic,source:registered ? "projects-ceo:registered" : "session-organization")],suggestedTitle:nil,summary:nil,source:"session-organization",sourceFingerprint:String(repeating:"b",count:64),conversationIDs:w.sessions.map(\.sessionId),analyzedAt:Date(),evidence:registered ? [.init(kind:"registered-project-reference",reference:"registered",sessionId:nil)] : nil)
  }
  return .init(schemaVersion:1,proposals:rows,diagnostics:[],registryFingerprint:registered ? String(repeating:"a",count:64) : nil)
 }
}
// Only the failure type is needed from the real transport implementation. The
// service seam deliberately tests native lifecycle, not subprocess behavior.
enum SidebarOrganizationService {enum Failure:Error {case invalidInput}}
@main struct Contract {
 @MainActor static func main() async throws {
  var checks:[String:Bool]=[:]
  func fixture(grouped:Bool=false,gmail:Bool=false)->(TabManager,Workspace,Analysis,Clock,SidebarOrganizationCoordinator){
   let m=TabManager(),w=Workspace(),other=Workspace(),service=Analysis(),clock=Clock();m.tabs=[w,other]
   if grouped {let g=UUID();m.workspaceGroups=[.init(id:g,name:"Manual HR",liveAnchorWorkspaceId:w.id)];w.groupId=g}
   if gmail {w.browserURL=URL(string:"https://mail.google.com/mail/u/0/#inbox")}
   return(m,w,service,clock,SidebarOrganizationCoordinator(service:service,now:{clock.next()}))
  }
  let(grouped,groupedRow,_,_,groupedCoordinator)=fixture(grouped:true)
  let groupedResult=await groupedCoordinator.analyze(tabManager:grouped)
  checks["unchanged grouped workspace analyzes and stores proposal"] = groupedResult.accepted && groupedRow.workspaceContext.context.analyzedProposal != nil && grouped.tabs.allSatisfy{$0.workspaceContext.context.revision==1}
  let(gmail,gmailRow,_,_,gmailCoordinator)=fixture(gmail:true)
  var gmailPassed=false
  do {let data=try await gmailCoordinator.export(tabManager:gmail);let decoder=JSONDecoder();decoder.dateDecodingStrategy = .iso8601;let exported=try decoder.decode(SidebarOrganizationInput.self,from:data);let result=await gmailCoordinator.analyze(tabManager:gmail,exportID:exported.id);gmailPassed=result.accepted && exported.workspaces[0].serviceObservations?.first?.service == "gmail" && gmailRow.workspaceContext.context.revision==1}catch{}
  checks["Gmail export and retained analyze ignore observation refresh time"] = gmailPassed
  let(bounded,_,boundedService,_,boundedCoordinator)=fixture(grouped:true,gmail:true)
  await boundedService.configure(trim:true)
  var boundedPassed=false
  do {let data=try await boundedCoordinator.export(tabManager:bounded);let decoder=JSONDecoder();decoder.dateDecodingStrategy = .iso8601;let input=try decoder.decode(SidebarOrganizationInput.self,from:data);let result=await boundedCoordinator.analyze(tabManager:bounded,exportID:input.id);boundedPassed=result.accepted && input.workspaces[0].groupName == nil && input.workspaces[0].serviceObservations?.isEmpty == true}catch{}
  checks["budget-removed enrichment compares original native export inventory"] = boundedPassed
  for mode in ["group-name","group-origin","gmail-disappears","browser-surface","source-account","source-uid","source-fingerprint","session","generation","directory","surface"] {
   let(m,w,service,_,coordinator)=fixture(grouped:mode.hasPrefix("group"),gmail:true)
   if mode.hasPrefix("source") {var state=w.workspaceContext.persisted;state.context.sourceReferences=[.init(accountRef:"MBX-"+String(repeating:"a",count:64),directoryUserId:"123456789",resourceId:"GTK-"+String(repeating:"b",count:64),messageId:"message-1",evidenceFingerprint:String(repeating:"c",count:64))];w.workspaceContext.restore(state);precondition(state.context.sourceReferences![0].isStructurallyValid)}
   let before=m.tabs.map{$0.workspaceContext.persisted}
   await service.configure(hook:{await MainActor.run {
    switch mode {
    case "group-name":m.workspaceGroups[0].name="Changed"
    case "group-origin":w.groupPlacement=SidebarOrganizationPlacement(planID:UUID())
    case "gmail-disappears":w.browserURL=URL(string:"https://example.com")
    case "browser-surface","surface":w.surfaces[0]=UUID()
    case "source-account","source-uid","source-fingerprint":var state=w.workspaceContext.persisted;var ref=state.context.sourceReferences![0];if mode == "source-account"{ref.accountRef="MBX-"+String(repeating:"d",count:64)};if mode == "source-uid"{ref.directoryUserId="987654321"};if mode == "source-fingerprint"{ref.evidenceFingerprint=String(repeating:"d",count:64)};state.context.sourceReferences=[ref];w.workspaceContext.restore(state)
    default:let old=w.sessions[0];w.sessions[0] = .init(toolId:old.toolId,sessionId:mode == "session" ? "replacement" : old.sessionId,directory:mode == "directory" ? "/other" : old.directory,title:old.title,context:nil,surfaceId:old.surfaceId,processGeneration:mode == "generation" ? 2 : old.processGeneration)
    }
   }})
   let result=await coordinator.analyze(tabManager:m)
   // Source metadata changes are the simulated external drift, not coordinator writes.
   checks[mode+" drift rejects entire batch before context writes"] = !result.accepted && result.rejectionReason == .revisionConflict && zip(m.tabs,before).allSatisfy{$0.workspaceContext.context.revision == $1.context.revision && $0.workspaceContext.context.analyzedProposal == $1.context.analyzedProposal} && m.writes==0
  }
  let(stale,_,staleService,_,staleCoordinator)=fixture(grouped:true)
  let data=try await staleCoordinator.export(tabManager:stale);let decoder=JSONDecoder();decoder.dateDecodingStrategy = .iso8601;let export=try decoder.decode(SidebarOrganizationInput.self,from:data)
  await staleService.configure(hook:{await MainActor.run{stale.workspaceGroups[0].name="Changed after export"}})
  let result=await staleCoordinator.analyze(tabManager:stale,exportID:export.id)
  checks["retained export still fences later manual folder rename"] = !result.accepted && stale.tabs.allSatisfy{$0.workspaceContext.context.revision==0}
  let selectionManager=TabManager(),selectionRow=Workspace(),excludedRow=Workspace(),selectionService=Analysis(),selectionProbe=Probe()
  selectionManager.tabs=[selectionRow,excludedRow]
  await selectionService.configure(registered:true)
  let selectionRegistry=SidebarOrganizationRegistryReader(engineURL:URL(fileURLWithPath:"/immutable/engine.py"),rulesURL:URL(fileURLWithPath:"/current/rules.yaml"),commands:selectionProbe,pythonCandidates:["/python"])
  let selectionCoordinator=SidebarOrganizationCoordinator(service:selectionService,registry:selectionRegistry)
  let selectionPlan=try await selectionCoordinator.preparePlan(tabManager:selectionManager,workspaceIDs:[selectionRow.id],authorized:{true})
  checks["actual coordinator preparePlan preserves requested subset and private ticket through issuer"] = selectionPlan.assignments.map(\.workspaceID) == [selectionRow.id] && selectionPlan.sourceFingerprint == String(repeating:"a",count:64) && selectionManager.writes == 0 && selectionRow.workspaceContext.context.revision == 1 && excludedRow.workspaceContext.context.revision == 0
  print(String(decoding:try JSONSerialization.data(withJSONObject:checks,options:.sortedKeys),as:UTF8.self))
 }
}
'''


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=120)


def main():
    with tempfile.TemporaryDirectory(prefix='native-coordinator-contract-') as directory:
        p = Path(directory)
        sdk = ROOT/'Packages/macOS/CmuxExtensionKit/Sources/CmuxExtensionKit/Sidebar'
        names = ['CmuxSidebarContextTag.swift', 'CmuxSidebarContextTagOrigin.swift', 'CmuxSidebarWorkspaceContextMutation.swift', 'CmuxSidebarWorkspaceContextProposal.swift', 'CmuxSidebarWorkspaceContext.swift', 'CmuxSidebarSourceMetadata.swift', 'CMUXExtensionActionResult.swift']
        run('xcrun', 'swiftc', '-swift-version', '6', '-emit-library', '-emit-module', '-module-name', 'CmuxExtensionKit', '-emit-module-path', str(p/'CmuxExtensionKit.swiftmodule'), '-o', str(p/'libCmuxExtensionKit.dylib'), *[str(sdk/n) for n in names])
        scrub = ROOT/'Packages/Shared/CmuxSentryTelemetry/Sources/CmuxSentryScrubbing'
        run('xcrun', 'swiftc', '-swift-version', '6', '-emit-library', '-emit-module', '-module-name', 'CmuxSentryScrubbing', '-emit-module-path', str(p/'CmuxSentryScrubbing.swiftmodule'), '-o', str(p/'libCmuxSentryScrubbing.dylib'), *map(str, scrub.glob('*.swift')))
        foundation = ROOT/'Packages/macOS/CmuxFoundation/Sources/CmuxFoundation/Process'
        (p/'Runner.swift').write_text('import Foundation\npublic struct CommandRunner:CommandRunning {public init(maximumCaptureBytes:Int?=nil){}\npublic func run(directory:String,executable:String,arguments:[String],timeout:TimeInterval?) async->CommandResult{fatalError("test injects protocol seam")}}\n')
        run('xcrun', 'swiftc', '-swift-version', '6', '-emit-library', '-emit-module', '-module-name', 'CmuxFoundation', '-emit-module-path', str(p/'CmuxFoundation.swiftmodule'), '-o', str(p/'libCmuxFoundation.dylib'), str(foundation/'CommandRunning.swift'), str(foundation/'CommandResult.swift'), str(p/'Runner.swift'))
        (p/'Contract.swift').write_text(HARNESS)
        files = ['WorkspaceContextModel.swift', 'SidebarOrganizationInput.swift', 'SidebarOrganizationOutput.swift', 'SidebarOrganizationPlan.swift', 'SidebarOrganizationPlanCoordinator.swift', 'SidebarOrganizationPlacement.swift', 'SidebarOrganizationPlacementLedger.swift', 'SidebarOrganizationNativeAdapter.swift', 'SidebarOrganizationRegistryReader.swift', 'SidebarOrganizationPlanIssuer.swift', 'SidebarOrganizationAnalyzing.swift', 'SidebarOrganizationInventoryBuilder.swift', 'SidebarOrganizationCoordinator.swift']
        run('xcrun', 'swiftc', '-swift-version', '6', '-I', str(p), '-L', str(p), '-lCmuxExtensionKit', '-lCmuxSentryScrubbing', '-lCmuxFoundation', '-Xlinker', '-rpath', '-Xlinker', str(p), *[str(ROOT/'Sources'/n) for n in files], str(p/'Contract.swift'), '-o', str(p/'contract'))
        checks = json.loads(run(str(p/'contract')).stdout)
        for name, passed in checks.items():
            print(('PASS ' if passed else 'FAIL ')+name)
        print(json.dumps({'passed': sum(checks.values()), 'failed': sum(not value for value in checks.values())}))
        return 0 if len(checks) == 16 and all(checks.values()) else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        print(error.stderr)
        raise SystemExit(2)
