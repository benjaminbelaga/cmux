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
  let checks=["same-group manual override holds before any core write":held && native.writes==writes && native.state.workspaces[1].groupID==native.group]
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
