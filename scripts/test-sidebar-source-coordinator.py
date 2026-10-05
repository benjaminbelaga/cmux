#!/usr/bin/env python3
"""Exercise native context/open fencing with real context model and resolver seam.

Only the window/workspace container is replaced to avoid Ghostty/app startup.
No provider authentication, browser opening, or native app claim is made.
"""
from pathlib import Path
import json
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''import AppKit
import Foundation
import CmuxExtensionKit
@MainActor final class Workspace {
 let id:UUID
 let workspaceContext=WorkspaceContextModel()
 init(_ id:UUID=UUID()){self.id=id}
}
@MainActor final class TabManager { var tabs:[Workspace];init(_ rows:[Workspace]){tabs=rows} }
@MainActor final class Opens {var urls:[URL]=[];func open(_ url:URL)->Bool{urls.append(url);return true}}
@main struct Contract {
 @MainActor static func main() async throws {
  let workspace=Workspace();let manager=TabManager([workspace]);let opens=Opens()
  let ref=CmuxSidebarSourceReference(accountRef:"MBX-"+String(repeating:"a",count:64),directoryUserId:"123",resourceId:"GTK-"+String(repeating:"b",count:64),messageId:"message",evidenceFingerprint:String(repeating:"c",count:64))
  let requestID="CEO-"+String(repeating:"a",count:32)
  let locator=SidebarSourceReferenceResolver.Locator(provider:"gmail",principal:"ben@yoyaku.fr",workspaceID:"yoyaku.fr",directoryUserID:"123",accountRef:ref.accountRef,gmailThreadKey:ref.resourceId,gmailThreadID:"thread",gmailMessageID:"message",url:"https://mail.google.com/mail/?authuser=ben%40yoyaku.fr#all/thread")
  let service=SidebarSourceReferenceCoordinator(resolveSource:{ _ in locator },openURL:{opens.open($0)})
  var checks:[String:Bool]=[:]
  let revision=try await service.attach(manager:manager,workspaceID:workspace.id,expectedRevision:0,requestID:requestID,reference:ref)
  checks["native post-attachment readback"] = revision == 1 && workspace.workspaceContext.context.sourceRequestBindings?.first?.attachedRevision == 1
  checks["attachment never opens"] = opens.urls.isEmpty
  let before=workspace.workspaceContext.persisted
  let duplicate=try await service.attach(manager:manager,workspaceID:workspace.id,expectedRevision:1,requestID:requestID,reference:ref)
  checks["verified duplicate idempotent"] = duplicate == 1 && before == workspace.workspaceContext.persisted
  try await service.open(manager:manager,workspaceID:workspace.id,expectedRevision:1,referenceFingerprint:ref.fingerprint!)
  checks["current retained source opens once"] = opens.urls.map(\.absoluteString) == [locator.url]
  let tag=CmuxSidebarContextTag(id:"topic:hr",label:"HR",dimension:"topic",origin:.manual,source:"user")
  try workspace.workspaceContext.mutate(expectedRevision:1,mutation:.setManualTag(tag))
  try await service.open(manager:manager,workspaceID:workspace.id,expectedRevision:2,referenceFingerprint:ref.fingerprint!)
  checks["later manual revision allowed"] = opens.urls.count == 2 && workspace.workspaceContext.context.tags == [tag]
  func fails(_ action:() async throws -> Void) async -> Bool {do{try await action();return false}catch{return true}}
  let count=opens.urls.count
  checks["stale click cannot open"] = await fails {try await service.open(manager:manager,workspaceID:workspace.id,expectedRevision:1,referenceFingerprint:ref.fingerprint!)} && opens.urls.count == count
  checks["unattached reference cannot open"] = await fails {try await service.open(manager:manager,workspaceID:workspace.id,expectedRevision:2,referenceFingerprint:String(repeating:"d",count:64))} && opens.urls.count == count
  checks["revoked grant cannot open"] = await fails {try await service.open(manager:manager,workspaceID:workspace.id,expectedRevision:2,referenceFingerprint:ref.fingerprint!,authorized:{false})} && opens.urls.count == count
  let held=SidebarSourceReferenceCoordinator(resolveSource:{_ in throw SidebarSourceReferenceResolver.Failure.held},openURL:{opens.open($0)})
  let preserved=workspace.workspaceContext.persisted
  checks["resolver hold preserves context"] = await fails {_ = try await held.attach(manager:manager,workspaceID:workspace.id,expectedRevision:2,requestID:requestID,reference:ref)} && preserved == workspace.workspaceContext.persisted && opens.urls.count == count
  let drift=SidebarSourceReferenceCoordinator(resolveSource:{ _ in
   await MainActor.run {try! workspace.workspaceContext.mutate(expectedRevision:2,mutation:.setSummary("Manual edit during verification"))};return locator
  },openURL:{opens.open($0)})
  checks["provider wait manual drift cannot open"] = await fails {try await drift.open(manager:manager,workspaceID:workspace.id,expectedRevision:2,referenceFingerprint:ref.fingerprint!)} && opens.urls.count == count
  let replace=SidebarSourceReferenceCoordinator(resolveSource:{ _ in
   await MainActor.run {let clone=Workspace(workspace.id);clone.workspaceContext.restore(workspace.workspaceContext.persisted);manager.tabs=[clone]};return locator
  },openURL:{opens.open($0)})
  checks["same UUID replacement cannot open"] = await fails {try await replace.open(manager:manager,workspaceID:workspace.id,expectedRevision:3,referenceFingerprint:ref.fingerprint!)} && opens.urls.count == count
  let current=manager.tabs[0]
  let rejectedOpen=SidebarSourceReferenceCoordinator(resolveSource:{ _ in locator },openURL:{_ in false})
  checks["opening failure never accepted"] = await fails {try await rejectedOpen.open(manager:manager,workspaceID:current.id,expectedRevision:3,referenceFingerprint:ref.fingerprint!)}
  let unsupported=Workspace();manager.tabs.append(unsupported)
  checks["metadata without binding cannot open"] = await fails {try await service.open(manager:manager,workspaceID:unsupported.id,expectedRevision:0,referenceFingerprint:ref.fingerprint!)} && opens.urls.count == count
  print(String(decoding:try JSONSerialization.data(withJSONObject:checks,options:.sortedKeys),as:UTF8.self))
 }
}
'''


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=120)


def main():
    with tempfile.TemporaryDirectory(prefix='native-source-coordinator-') as temp:
        p = Path(temp)
        sdk = ROOT / 'Packages/macOS/CmuxExtensionKit/Sources/CmuxExtensionKit/Sidebar'
        names = ['CmuxSidebarContextTag.swift', 'CmuxSidebarContextTagOrigin.swift', 'CmuxSidebarWorkspaceContextMutation.swift', 'CmuxSidebarWorkspaceContextProposal.swift', 'CmuxSidebarWorkspaceContext.swift', 'CmuxSidebarSourceMetadata.swift']
        run('xcrun', 'swiftc', '-swift-version', '6', '-emit-library', '-emit-module', '-module-name', 'CmuxExtensionKit', '-emit-module-path', str(p/'CmuxExtensionKit.swiftmodule'), '-o', str(p/'libCmuxExtensionKit.dylib'), *[str(sdk/n) for n in names])
        # The actual resolver request/locator definitions are copied from the
        # already-tested shared leaf; its subprocess implementation is not used.
        resolver = (ROOT/'Sources/SidebarSourceReferenceResolver.swift').read_text()
        shape = resolver[resolver.index('    enum Failure:'):resolver.index('    private struct Report:')]
        (p/'Resolver.swift').write_text('import Foundation\nimport CmuxExtensionKit\nactor SidebarSourceReferenceResolver {\n'+shape+'\nfunc resolve(_ request:Request) async throws -> Locator {throw Failure.held}\n}\n')
        (p/'Contract.swift').write_text(HARNESS)
        run('xcrun', 'swiftc', '-swift-version', '6', '-I', str(p), '-L', str(p), '-lCmuxExtensionKit', '-Xlinker', '-rpath', '-Xlinker', str(p), str(ROOT/'Sources/WorkspaceContextModel.swift'), str(p/'Resolver.swift'), str(ROOT/'Sources/SidebarSourceReferenceCoordinator.swift'), str(p/'Contract.swift'), '-o', str(p/'contract'))
        checks = json.loads(run(str(p/'contract')).stdout)
        for name, passed in checks.items():
            print(('PASS ' if passed else 'FAIL ')+name)
        print(json.dumps({'passed': sum(checks.values()), 'failed': sum(not x for x in checks.values())}))
        return 0 if len(checks) == 13 and all(checks.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
