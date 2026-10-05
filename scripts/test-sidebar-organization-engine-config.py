#!/usr/bin/env python3
"""Validate the actual isolated bundle's immutable engine configuration leaf."""
from pathlib import Path
import json
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''import Foundation
import CryptoKit
@main struct Contract {
 static func main() throws {
  let root=FileManager.default.temporaryDirectory.appendingPathComponent("engine-config-"+UUID().uuidString).resolvingSymlinksInPath()
  try FileManager.default.createDirectory(at:root,withIntermediateDirectories:false)
  defer{try? FileManager.default.removeItem(at:root)}
  let source=String(repeating:"a",count:40)
  let directory=root.appendingPathComponent(".local/share/cmux-session-organization/"+source)
  try FileManager.default.createDirectory(at:directory,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
  let engine=directory.appendingPathComponent("session-organization.py")
  let code=Data("print('immutable')\n".utf8)
  try code.write(to:engine);try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:engine.path)
  let hash=SHA256.hash(data:code).map{String(format:"%02x",$0)}.joined()
  let rules=root.appendingPathComponent("repos/ecosystem/inventory/session-organization.yaml")
  var serial=0
  func bundle(_ changes:[String:String]=[:],id:String="com.cmuxterm.app.debug.organization") throws -> Bundle {
   serial+=1;let app=root.appendingPathComponent("Fixture-\(serial).app/Contents")
   try FileManager.default.createDirectory(at:app,withIntermediateDirectories:true)
   var environment=["CMUX_ORGANIZATION_ENGINE_SOURCE":source,"CMUX_ORGANIZATION_ENGINE_SHA256":hash,"CMUX_ORGANIZATION_ENGINE_PATH":engine.path,"CMUX_ORGANIZATION_RULES_PATH":rules.path]
   for(k,v) in changes{environment[k]=v}
   let info:[String:Any]=["CFBundleIdentifier":id,"CFBundlePackageType":"APPL","LSEnvironment":environment]
   try PropertyListSerialization.data(fromPropertyList:info,format:.xml,options:0).write(to:app.appendingPathComponent("Info.plist"))
   return Bundle(path:app.deletingLastPathComponent().path)!
  }
  func accepted(_ changes:[String:String]=[:],id:String="com.cmuxterm.app.debug.organization") throws -> Bool {
   SidebarOrganizationEngineConfiguration(bundle:try bundle(changes,id:id),homeDirectory:root) != nil
  }
  var checks:[String:Bool]=[:]
  let configured=SidebarOrganizationEngineConfiguration(bundle:try bundle(),homeDirectory:root)
  checks["exact owned immutable source and fresh canonical rules accepted"] = configured?.engineURL == engine && configured?.rulesURL == rules
  checks["production bundle cannot enable isolated plan configuration"] = try !accepted(id:"com.cmuxterm.app")
  checks["caller path cannot replace registered engine"] = try !accepted(["CMUX_ORGANIZATION_ENGINE_PATH":"/other/engine.py"])
  checks["snapshot rules cannot impersonate current registry"] = try !accepted(["CMUX_ORGANIZATION_RULES_PATH":"/snapshot/session-organization.yaml"])
  checks["wrong source bytes hold"] = try !accepted(["CMUX_ORGANIZATION_ENGINE_SHA256":String(repeating:"b",count:64)])
  checks["trailing newline source and fingerprint hold"] = try !accepted(["CMUX_ORGANIZATION_ENGINE_SOURCE":source+"\n"]) && !accepted(["CMUX_ORGANIZATION_ENGINE_SHA256":hash+"\n"])
  try FileManager.default.setAttributes([.posixPermissions:0o644],ofItemAtPath:engine.path)
  checks["public engine mode holds"] = try !accepted()
  try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:engine.path)
  try FileManager.default.setAttributes([.posixPermissions:0o755],ofItemAtPath:directory.path)
  checks["public engine directory holds"] = try !accepted()
  try FileManager.default.setAttributes([.posixPermissions:0o700],ofItemAtPath:directory.path)
  try Data("changed".utf8).write(to:engine)
  checks["later engine code drift holds"] = try !accepted()
  try code.write(to:engine);try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:engine.path)
  let actual=root.appendingPathComponent("actual.py");try FileManager.default.moveItem(at:engine,to:actual)
  try FileManager.default.createSymbolicLink(at:engine,withDestinationURL:actual)
  checks["symlink cannot supply immutable source"] = try !accepted()
  print(String(decoding:try JSONSerialization.data(withJSONObject:checks,options:.sortedKeys),as:UTF8.self))
 }
}
'''


def main():
    with tempfile.TemporaryDirectory(prefix='engine-configuration-contract-') as directory:
        p = Path(directory)
        (p/'Contract.swift').write_text(HARNESS)
        result = subprocess.run(['xcrun', 'swiftc', '-swift-version', '6',
                                 str(ROOT/'Sources/SidebarOrganizationEngineConfiguration.swift'),
                                 str(p/'Contract.swift'), '-o', str(p/'contract')],
                                capture_output=True, text=True, timeout=120)
        if result.returncode:
            print(result.stderr)
            return 2
        result = subprocess.run([str(p/'contract')], check=True, capture_output=True, text=True, timeout=20)
        checks = json.loads(result.stdout)
        for name, passed in checks.items():
            print(('PASS ' if passed else 'FAIL ')+name)
        print(json.dumps({'passed': sum(checks.values()), 'failed': sum(not v for v in checks.values())}))
        return 0 if len(checks) == 10 and all(checks.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
