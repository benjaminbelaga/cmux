#!/usr/bin/env python3
"""Execute the exact TabManager reorder entrypoint bodies and real placement type.

Native reorder completion and app containers are fixtures. This tests placement
provenance in actual operation callers; it is not an AppKit gesture/app proof.
"""
from pathlib import Path
import json,re,subprocess,tempfile
ROOT=Path(__file__).resolve().parents[1]
HARNESS=r"""import Foundation
final class Workspace {
 let id=UUID();var groupId:UUID?;var groupPlacement:SidebarOrganizationPlacement?
 init(group:UUID,plan:UUID){groupId=group;groupPlacement = .init(planID:plan)}
}
struct Group {let liveAnchorWorkspaceId:UUID?}
final class Reordering {
 var handled=true
 func reorderSidebarWorkspace(tabId:UUID,toIndex:Int,isDragOperation:Bool,usesTopLevelRows:Bool,explicitGroupId:UUID?)->Bool{handled}
 func reorderSidebarWorkspaces(tabIds:[UUID],draggedTabId:UUID,toIndex:Int,isDragOperation:Bool,usesTopLevelRows:Bool,explicitGroupId:UUID?)->Bool{handled}
}
final class TabManager {
 var tabs:[Workspace]=[];var workspaceGroups:[Group]=[];let workspaceReordering=Reordering()
 func workspaceGroupMemberships(for ids:[UUID])->[UUID:UUID]{[:]} 
 func cleanupGeneratedAnchorsAfterWorkspaceRemoval(previousMemberships:[UUID:UUID]){}
 METHODS
}
@main struct Contract {
 static func main() throws {
  var values:[String:Bool]=[:]
  func fixture()->(TabManager,UUID,UUID){let group=UUID(),plan=UUID(),manager=TabManager();manager.tabs=(0..<3).map{_ in Workspace(group:group,plan:plan)};return(manager,group,plan)}
  let(single,g,_)=fixture()
  _=single.reorderSidebarWorkspace(tabId:single.tabs[0].id,toIndex:0,isDragOperation:true,explicitGroupId:g)
  values["accepted explicit same-group native drop becomes manual"] = single.tabs[0].groupPlacement == nil
  let(batch,b,plan)=fixture();_=batch.reorderSidebarWorkspaces(tabIds:batch.tabs.prefix(2).map(\.id),draggedTabId:batch.tabs[0].id,toIndex:0,isDragOperation:true,explicitGroupId:b)
  values["accepted explicit block changes only requested members"] = batch.tabs[0].groupPlacement == nil && batch.tabs[1].groupPlacement == nil && batch.tabs[2].groupPlacement?.planID == plan
  for mode in ["implicit","programmatic","failed","anchor"] {
   let(m,group,p)=fixture();if mode == "failed"{m.workspaceReordering.handled=false};if mode == "anchor"{m.workspaceGroups=[.init(liveAnchorWorkspaceId:m.tabs[0].id)]}
   _=m.reorderSidebarWorkspace(tabId:m.tabs[0].id,toIndex:0,isDragOperation:mode != "programmatic",explicitGroupId:mode == "implicit" ? nil : group)
   values[mode+" reorder preserves automatic placement"] = m.tabs[0].groupPlacement?.planID == p
  }
  let(mixed,mg,mp)=fixture();mixed.workspaceGroups=[.init(liveAnchorWorkspaceId:mixed.tabs[0].id)]
  _=mixed.reorderSidebarWorkspaces(tabIds:mixed.tabs.prefix(2).map(\.id),draggedTabId:mixed.tabs[1].id,toIndex:0,isDragOperation:true,explicitGroupId:mg)
  values["native anchor remains intact in mixed requested block"] = mixed.tabs[0].groupPlacement?.planID == mp && mixed.tabs[1].groupPlacement == nil
  let(anchorBlock,ab,ap)=fixture();anchorBlock.workspaceGroups=[.init(liveAnchorWorkspaceId:anchorBlock.tabs[0].id)]
  _=anchorBlock.reorderSidebarWorkspaces(tabIds:anchorBlock.tabs.prefix(2).map(\.id),draggedTabId:anchorBlock.tabs[0].id,toIndex:0,isDragOperation:true,explicitGroupId:ab)
  values["native anchor-only block excludes requested nonparticipating children"] = anchorBlock.tabs.prefix(2).allSatisfy{$0.groupPlacement?.planID == ap}
  let(mismatch,mismatchGroup,mismatchPlan)=fixture();mismatch.tabs[0].groupId=UUID()
  _=mismatch.reorderSidebarWorkspace(tabId:mismatch.tabs[0].id,toIndex:0,isDragOperation:true,explicitGroupId:mismatchGroup)
  values["operation without actual target membership grants no takeover"] = mismatch.tabs[0].groupPlacement?.planID == mismatchPlan
  print(String(decoding:try JSONSerialization.data(withJSONObject:values,options:.sortedKeys),as:UTF8.self))
 }
}
"""
def actual_method(source,name):
 match=re.search(r'^    (?:private )?func '+re.escape(name)+r'\(',source,re.MULTILINE)
 if not match:return None
 opening=source.index('{',match.start());depth=0
 for end in range(opening,len(source)):
  depth += (source[end]=='{')-(source[end]=='}')
  if depth==0:return source[match.start():end+1]
 raise ValueError('Unbalanced actual method '+name)
def main():
 source=(ROOT/'Sources/TabManager.swift').read_text()
 names=['reorderSidebarWorkspace','reorderSidebarWorkspaces','clearAutomaticPlacementForExplicitSidebarDrop']
 methods=[actual_method(source,name) for name in names]
 if not all(methods[:2]):raise ValueError('Actual native reorder entrypoints absent')
 with tempfile.TemporaryDirectory(prefix='native-manual-group-placement-') as directory:
  root=Path(directory);(root/'Contract.swift').write_text(HARNESS.replace('METHODS','\n'.join(m for m in methods if m)))
  binary=root/'contract'
  run=subprocess.run(['xcrun','swiftc','-swift-version','6',str(ROOT/'Sources/SidebarOrganizationPlacement.swift'),str(root/'Contract.swift'),'-o',str(binary)],capture_output=True,text=True,timeout=90)
  if run.returncode:print(run.stderr);return 2
  values=json.loads(subprocess.run([str(binary)],check=True,capture_output=True,text=True,timeout=10).stdout)
  for name,passed in values.items():print(('PASS ' if passed else 'FAIL ')+name)
  print(json.dumps({'passed':sum(values.values()),'failed':sum(not v for v in values.values())}))
  return 0 if len(values)==9 and all(values.values()) else 1
if __name__=='__main__':raise SystemExit(main())
