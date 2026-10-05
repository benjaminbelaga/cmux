#!/usr/bin/env python3
"""Compile the retained native core and exercise the outer placement fence.

Before the new leaf exists, the fixture invokes the actual previous core path.
Pinned source is compiled in a temporary directory; no application is launched.
"""
from pathlib import Path
import json
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CORE = 'dc2c8cf54428efa20a08d7b8a2a319aa588e3c12'
HARNESS = r'''import Foundation
@MainActor final class Native: SidebarOrganizationPlanCoordinator.Adapter {
 typealias P=SidebarOrganizationPlan
 let target=UUID();let existing=UUID();let group=UUID()
 var state:P.Inventory
 var placement:SidebarOrganizationPlacement?
 var writes=0
 init(){
  state = .init(windowID:UUID(),workspaces:[],groups:[],order:[],selectionFingerprint:String(repeating:"a",count:64))
  state.workspaces=[.init(id:existing,revision:0,title:"Existing",metadataFingerprint:String(repeating:"b",count:64),groupID:group,generatedAnchor:false),.init(id:target,revision:0,title:"Target",metadataFingerprint:String(repeating:"b",count:64),groupID:nil,generatedAnchor:false)]
  state.groups=[.init(id:group,name:"HR",externalID:nil,anchorID:existing,generatedAnchor:false,pinned:false,collapsed:false,metadataFingerprint:String(repeating:"c",count:64),members:[existing])]
  state.order=[existing,target]
 }
 func inventory() throws -> P.Inventory{state}
 func sourceFingerprint() throws -> String {String(repeating:"d",count:64)}
 func createGroup(name:String,children:[UUID],externalID:String)throws->UUID{throw P.Failure.mutationFailed}
 func addWorkspace(_ id:UUID,to group:UUID)throws {writes+=1;state.workspaces[1].groupID=group;state.groups[0].members.append(id)}
 func removeWorkspace(_ id:UUID)throws {writes+=1;state.workspaces[1].groupID=nil;state.groups[0].members.removeAll{$0==id}}
 func ungroup(_ id:UUID)throws {throw P.Failure.mutationFailed}
 func restoreOrder(_ order:[UUID])throws{writes+=1;state.order=order}
}
@main struct Contract {
 @MainActor static func main() throws {
  let native=Native(),core=SidebarOrganizationPlanCoordinator()
  let plan=try SidebarOrganizationPlan(sourceFingerprint:native.sourceFingerprint(),before:native.state,assignments:[.init(workspaceID:native.target,destination:.existing(native.group),evidence:.registeredRepository)])
  let receipt=try core.apply(plan,using:native)
  native.placement = .init(planID:plan.id)
  #if PLACEMENT_LEDGER
  let ledger=SidebarOrganizationPlacementLedger()
  let before=[SidebarOrganizationPlacementLedger.Snapshot(workspaceID:native.target,groupID:nil,placement:nil)]
  let after=[SidebarOrganizationPlacementLedger.Snapshot(workspaceID:native.target,groupID:native.group,placement:native.placement)]
  let retained=try ledger.issue(planID:plan.id,coreReceiptID:receipt.plan.id,before:before,expectedAfter:after,observedAfter:after)
  #endif
  // A manual choice of the same group leaves core topology unchanged.
  native.placement=nil
  let writes=native.writes
  var held=false
  do {
   #if PLACEMENT_LEDGER
   _ = try ledger.beforeRollback(retained,observed:[.init(workspaceID:native.target,groupID:native.group,placement:native.placement)])
   #endif
   try core.rollback(receipt,using:native)
  } catch {held=true}
  var checks=["same-group manual override holds before any core write":held && native.writes==writes && native.state.workspaces[1].groupID==native.group]
  #if PLACEMENT_LEDGER
  typealias L=SidebarOrganizationPlacementLedger
  func rejects(_ action:() throws -> Void)->Bool{do{try action();return false}catch{return true}}
  func fixture(_ ledger:L, planID:UUID=UUID()) throws -> L.Receipt {
   let id=UUID(),group=UUID()
   return try ledger.issue(planID:planID,coreReceiptID:planID,
    before:[.init(workspaceID:id,groupID:nil,placement:nil)],
    expectedAfter:[.init(workspaceID:id,groupID:group,placement:.init(planID:planID))],
    observedAfter:[.init(workspaceID:id,groupID:group,placement:.init(planID:planID))])
  }
  let normal=L(),good=try fixture(normal)
  checks["finish cannot skip preflight"] = rejects{try normal.finishRollback(good,observed:good.before)}
  checks["verified preflight returns exact legacy nil"] = try normal.beforeRollback(good,observed:good.after) == good.before && good.before[0].placement == nil
  try normal.finishRollback(good,observed:good.before)
  checks["successful restoration consumes exact receipt"] = rejects{_ = try normal.beforeRollback(good,observed:good.after)}
  let wrong=L(),valid=try fixture(wrong)
  let forged=try L().issue(planID:valid.planID,coreReceiptID:valid.coreReceiptID,before:valid.before,expectedAfter:valid.after,observedAfter:valid.after)
  checks["forged issuance cannot rollback"] = rejects{_ = try wrong.beforeRollback(forged,observed:valid.after)}
  checks["duplicate plan cannot reissue"] = rejects{_ = try wrong.issue(planID:valid.planID,coreReceiptID:valid.planID,before:valid.before,expectedAfter:valid.after,observedAfter:valid.after)}
  checks["core identity must match plan"] = rejects{_ = try L().issue(planID:UUID(),coreReceiptID:UUID(),before:valid.before,expectedAfter:valid.after,observedAfter:valid.after)}
  let newPlan=UUID()
  let actual=[L.Snapshot(workspaceID:valid.before[0].workspaceID,groupID:UUID(),placement:.init(planID:newPlan))]
  checks["unverified native readback cannot issue"] = rejects{_ = try L().issue(planID:newPlan,coreReceiptID:newPlan,before:valid.before,expectedAfter:actual,observedAfter:valid.before)}
  checks["duplicate workspace snapshots rejected"] = rejects{_ = try wrong.beforeRollback(valid,observed:valid.after+valid.after)}
  checks["other workspace cannot substitute"] = rejects{_ = try wrong.beforeRollback(valid,observed:[.init(workspaceID:UUID(),groupID:valid.after[0].groupID,placement:valid.after[0].placement)])}
  let manual=try JSONDecoder().decode(SidebarOrganizationPlacement.self,from:Data("{\"origin\":\"manual\",\"planID\":null}".utf8))
  checks["explicit manual same-group provenance is drift"] = rejects{_ = try wrong.beforeRollback(valid,observed:[.init(workspaceID:valid.after[0].workspaceID,groupID:valid.after[0].groupID,placement:manual)])}
  _ = try wrong.beforeRollback(valid,observed:valid.after)
  checks["failed restoration never consumes receipt"] = rejects{try wrong.finishRollback(valid,observed:valid.after)}
  try wrong.recordRecovery(planID:valid.planID,phase:.placementRestore,cause:.outcomeDiffers,before:valid.before,expected:valid.before,observed:valid.after)
  checks["unknown restoration blocks preflight and finish"] = rejects{_ = try wrong.beforeRollback(valid,observed:valid.after)} && rejects{try wrong.finishRollback(valid,observed:valid.before)}
  checks["recovery preserves exact actual observation"] = wrong.recoveries.last?.observed == valid.after && wrong.recoveries.last?.before == valid.before
  let bounded=L();let oldest=try fixture(bounded)
  for _ in 0..<64 {_ = try fixture(bounded)}
  checks["evicted receipt is closed"] = rejects{_ = try bounded.beforeRollback(oldest,observed:oldest.after)}
  let diagnostics=L()
  for _ in 0..<40 {try diagnostics.recordRecovery(planID:UUID(),phase:.applyReadback,cause:.observedUnavailable,before:valid.before,expected:valid.after,observed:nil)}
  checks["diagnostic retention is bounded and unavailable stays nil"] = diagnostics.recoveries.count == 32 && diagnostics.recoveries.allSatisfy{$0.observed == nil}
  let saturated=L(),unknownPlan=UUID()
  try saturated.recordRecovery(planID:unknownPlan,phase:.applyReadback,cause:.observedUnavailable,before:valid.before,expected:valid.after,observed:nil)
  for _ in 0..<100 {try saturated.recordRecovery(planID:UUID(),phase:.applyReadback,cause:.observedUnavailable,before:valid.before,expected:valid.after,observed:nil)}
  checks["bounded recovery saturation never clears an old retry hold"] = rejects{_ = try fixture(saturated,planID:unknownPlan)}
  let oversized=(0..<257).map{_ in L.Snapshot(workspaceID:UUID(),groupID:nil,placement:nil)}
  checks["affected snapshot budget cannot be exceeded"] = rejects{_ = try L().issue(planID:newPlan,coreReceiptID:newPlan,before:oversized,expectedAfter:oversized,observedAfter:oversized)}
  let successNative=Native(),successCore=SidebarOrganizationPlanCoordinator(),successLedger=L()
  let successPlan=try SidebarOrganizationPlan(sourceFingerprint:successNative.sourceFingerprint(),before:successNative.state,assignments:[.init(workspaceID:successNative.target,destination:.existing(successNative.group),evidence:.registeredRepository)])
  let successBefore=[L.Snapshot(workspaceID:successNative.target,groupID:nil,placement:nil)]
  let successReceipt=try successCore.apply(successPlan,using:successNative)
  successNative.placement = .init(planID:successPlan.id)
  let successAfter=[L.Snapshot(workspaceID:successNative.target,groupID:successNative.group,placement:successNative.placement)]
  let successRetained=try successLedger.issue(planID:successPlan.id,coreReceiptID:successReceipt.plan.id,before:successBefore,expectedAfter:successAfter,observedAfter:successAfter)
  let restore=try successLedger.beforeRollback(successRetained,observed:successAfter)
  try successCore.rollback(successReceipt,using:successNative)
  successNative.placement=restore[0].placement
  try successLedger.finishRollback(successRetained,observed:[.init(workspaceID:successNative.target,groupID:successNative.state.workspaces[1].groupID,placement:successNative.placement)])
  checks["real core rollback preserves unrelated manual member"] = successNative.state == successPlan.before && successNative.placement == nil
  #endif
  print(String(decoding:try JSONSerialization.data(withJSONObject:checks,options:.sortedKeys),as:UTF8.self))
 }
}
'''

def main():
    with tempfile.TemporaryDirectory(prefix='placement-ledger-contract-') as directory:
        temp = Path(directory)
        files = []
        for name in ('SidebarOrganizationPlan.swift', 'SidebarOrganizationPlanCoordinator.swift'):
            data = subprocess.run(['git', 'show', CORE + ':Sources/' + name], cwd=ROOT,
                                  check=True, capture_output=True).stdout
            source = temp / name
            source.write_bytes(data)
            files.append(str(source))
        harness = temp / 'Contract.swift'
        harness.write_text(HARNESS)
        files += [str(ROOT / 'Sources/SidebarOrganizationPlacement.swift'), str(harness)]
        ledger = ROOT / 'Sources/SidebarOrganizationPlacementLedger.swift'
        flags = []
        if ledger.exists():
            files.append(str(ledger))
            flags = ['-D', 'PLACEMENT_LEDGER']
        result = subprocess.run(['xcrun', 'swiftc', '-swift-version', '6', *flags, *files,
                                 '-o', str(temp / 'contract')], capture_output=True, text=True, timeout=120)
        if result.returncode:
            print(result.stderr)
            return 2
        result = subprocess.run([str(temp / 'contract')], capture_output=True, text=True,
                                check=True, timeout=20)
        checks = json.loads(result.stdout)
        for name, passed in checks.items():
            print(('PASS ' if passed else 'FAIL ') + name)
        print(json.dumps({'passed': sum(checks.values()), 'failed': sum(not value for value in checks.values())}))
        return 0 if all(checks.values()) else 1

if __name__ == '__main__':
    raise SystemExit(main())
