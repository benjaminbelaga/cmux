#!/usr/bin/env python3
"""Exercise actual native diagnostic generation/grant state without an app."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'Packages/macOS/CmuxSidebar/Sources/CmuxSidebar/ExtensionHost/Hosting/CMUXSidebarRecoveryDiagnostics.swift'
HARNESS=r'''import Foundation
@main struct Contract {
 @MainActor static func main() throws {
  let defaults=UserDefaults(suiteName:"runtime-grants-"+UUID().uuidString)!
  let selected="fixture.extension"; defaults.set(selected,forKey:"cmuxExtensionSidebar.selectedExtensionBundleId")
  let id=UUID(); let other=UUID()
  let d=CMUXSidebarRecoveryDiagnostics(defaults:defaults,processID:42,appVersion:"test",appBuild:"1",now:{Date(timeIntervalSince1970:0)})
  func row()->[String:Any] { (d.status(providerID:"cmux.sidebar.extensions",providerActive:true)["hosts"] as! [[String:Any]]).first! }
  func lifecycle(_ generation:UInt64,_ state:String="connected") { d.record(hostID:id,bundleID:selected,identityID:"identity",generation:generation,event:"test",state:state,code:nil) }
  func grant(_ mount:UInt64,_ connection:UInt64,_ revision:UInt64,_ scopes:[String]=["workspaceList"],_ manifest:String?="manifest") {
   d.runtimeGrant(hostID:id,generation:mount,connectionGeneration:connection,grantRevision:revision,manifestID:manifest,apiMajor:manifest == nil ? nil:2,apiMinor:manifest == nil ? nil:3,readScopes:scopes,actionScopes:manifest == nil ? []:["workspaceSelect"])
  }
  var r:[String:Bool]=[:]
  lifecycle(1)
  defaults.set(["readScopes":["configured-only"]],forKey:"cmuxExtensionSidebar.grants.v1")
  r["configuredDefaultsAreNotEffective"] = row()["effective_read_scopes"] == nil
  grant(1,10,1)
  r["actualCachePublished"] = row()["effective_read_scopes"] as? [String] == ["workspaceList"] && row()["grant_revision"] as? UInt64 == 1
  r["newGrantNeedsFreshAck"] = d.status(providerID:"cmux.sidebar.extensions",providerActive:true)["connected"] as? Bool == false
  lifecycle(1)
  r["freshAckConnects"] = d.status(providerID:"cmux.sidebar.extensions",providerActive:true)["connected"] as? Bool == true
  grant(1,10,2,[],nil)
  r["revokeClearsScopesAndConnection"] = row()["effective_read_scopes"] as? [String] == [] && row()["state"] as? String == "blocked"
  grant(1,10,1,["stale-authority"])
  r["oldGrantRevisionCannotRestoreAuthority"] = row()["effective_read_scopes"] as? [String] == [] && row()["grant_revision"] as? UInt64 == 2
  grant(1,11,1)
  grant(1,10,99,["old-connection"])
  r["oldConnectionCannotRestoreAuthority"] = row()["connection_generation"] as? UInt64 == 11 && row()["effective_read_scopes"] as? [String] == ["workspaceList"]
  lifecycle(2,"connecting")
  r["mountedGenerationClearsGrant"] = row()["manifest_id"] == nil && row()["effective_read_scopes"] == nil
  grant(1,99,99,["previous-mount"])
  r["previousMountCannotPublishGrant"] = row()["effective_read_scopes"] == nil
  grant(2,12,0)
  r["newMountPublishesOwnGrant"] = row()["connection_generation"] as? UInt64 == 12 && row()["grant_revision"] as? UInt64 == 0
  d.remove(id)
  d.runtimeGrant(hostID:id,generation:2,connectionGeneration:12,grantRevision:1,manifestID:"ghost",apiMajor:2,apiMinor:3,readScopes:["ghost"],actionScopes:[])
  r["dismantledHostCannotReappear"] = (d.status(providerID:"cmux.sidebar.extensions",providerActive:true)["hosts"] as! [[String:Any]]).isEmpty
  print(String(decoding:try JSONSerialization.data(withJSONObject:r,options:[.sortedKeys]),as:UTF8.self))
 }
}
'''
class RuntimeGrantTests(unittest.TestCase):
 def test_native_effective_grants_revoke_and_generation_fences(self):
  with tempfile.TemporaryDirectory(prefix='native-grant-contract-') as temp:
   root=Path(temp);(root/'Contract.swift').write_text(HARNESS);binary=root/'contract'
   subprocess.run(['xcrun','swiftc','-swift-version','6',str(SOURCE),str(root/'Contract.swift'),'-o',str(binary)],check=True,capture_output=True,text=True,timeout=120)
   values=json.loads(subprocess.run([str(binary)],check=True,capture_output=True,text=True,timeout=15).stdout)
   self.assertEqual(len(values),11)
   for name,passed in values.items():
    with self.subTest(name=name):self.assertTrue(passed)
if __name__=='__main__':unittest.main()
