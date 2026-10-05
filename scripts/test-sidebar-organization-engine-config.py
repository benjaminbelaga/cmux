#!/usr/bin/env python3
"""Validate the actual isolated bundle's immutable engine configuration leaf."""
from pathlib import Path
import json
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''import Foundation
import CryptoKit
import CmuxFoundation
actor Probe:CommandRunning {
 let engine:URL;let rules:URL;let mode:String
 var calls=0;var samePaths=true;var privatePackets=true
 init(engine:URL,rules:URL,mode:String="ok"){self.engine=engine;self.rules=rules;self.mode=mode}
 func run(directory:String,executable:String,arguments:[String],timeout:TimeInterval?) async -> CommandResult {
  calls+=1
  let python=arguments.first == "-c"
  if !python {
   samePaths = samePaths && arguments.first == engine.path && arguments.contains(rules.path)
   if let index=arguments.firstIndex(of:"--input") {
    let attributes=try! FileManager.default.attributesOfItem(atPath:arguments[index+1])
    let directoryAttributes=try! FileManager.default.attributesOfItem(atPath:directory)
    privatePackets = privatePackets && (attributes[.posixPermissions] as? NSNumber)?.intValue == 0o600
     && (directoryAttributes[.posixPermissions] as? NSNumber)?.intValue == 0o700
   }
  }
  if (python && mode == "python-drift") || (!python && mode == "engine-drift") {
   try! Data("changed after cached configuration".utf8).write(to:engine)
  }
  var object:[String:Any]=["schemaVersion":1,"registryFingerprint":String(repeating:"a",count:64)]
  if arguments.contains("--registry-fingerprint"){object["authority"]="none"}
  else {object["proposals"]=[];object["diagnostics"]=[]}
  let data=try! JSONSerialization.data(withJSONObject:object)
  if let index=arguments.firstIndex(of:"--output"){try! data.write(to:URL(fileURLWithPath:arguments[index+1]))}
  return .init(stdout:python ? nil : String(decoding:data,as:UTF8.self),stderr:nil,exitStatus:0,timedOut:false,executionError:nil)
 }
}
@main struct Contract {
 static func main() async throws {
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
  checks["cached configuration rechecks changed mode"] = configured?.isCurrent() == false
  try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:engine.path)
  try FileManager.default.setAttributes([.posixPermissions:0o755],ofItemAtPath:directory.path)
  checks["public engine directory holds"] = try !accepted()
  try FileManager.default.setAttributes([.posixPermissions:0o700],ofItemAtPath:directory.path)
  try Data("changed".utf8).write(to:engine)
  checks["later engine code drift holds"] = try !accepted()
  checks["cached configuration rechecks changed bytes"] = configured?.isCurrent() == false
  try code.write(to:engine);try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:engine.path)
  let actual=root.appendingPathComponent("actual.py");try FileManager.default.moveItem(at:engine,to:actual)
  try FileManager.default.createSymbolicLink(at:engine,withDestinationURL:actual)
  checks["symlink cannot supply immutable source"] = try !accepted()
  try FileManager.default.removeItem(at:engine);try FileManager.default.moveItem(at:actual,to:engine)
  let seal=configured!
  func restore() throws {try code.write(to:engine);try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:engine.path)}
  func rejects(_ action:() async throws -> Void) async->Bool {do{try await action();return false}catch{return true}}
  let input=SidebarOrganizationInput(id:UUID(),windowID:UUID(),createdAt:Date(),workspaces:[.init(id:UUID().uuidString,title:"Fixture",revision:0,groupId:nil,tags:[],aliases:[],summary:nil,rejectedAutomaticTagIDs:[],rejectedSourceFingerprints:[],sessions:[])])
  let probe=Probe(engine:engine,rules:rules)
  let reader=SidebarOrganizationRegistryReader(engineURL:seal.engineURL,rulesURL:seal.rulesURL,commands:probe,engineValidation:{seal.isCurrent()},pythonCandidates:["/python"])
  let service=SidebarOrganizationService(commands:probe,homeDirectory:root,temporaryDirectory:root,engineURL:seal.engineURL,rulesURL:seal.rulesURL,engineValidation:{seal.isCurrent()},pythonCandidates:["/python"])
  _ = try await reader.read();_ = try await service.analyze(input)
  checks["actual reader and service use same sealed code current rules"] = await probe.samePaths
  checks["actual classification packets are private"] = await probe.privatePackets
  try Data("changed cached code".utf8).write(to:engine)
  let calls=await probe.calls
  let readerHeld=await rejects{_ = try await reader.read()};let readerCalls=await probe.calls
  checks["long-lived reader rejects code drift before process"] = readerHeld && readerCalls == calls
  let serviceHeld=await rejects{_ = try await service.analyze(input)};let serviceCalls=await probe.calls
  checks["long-lived service rejects code drift before process"] = serviceHeld && serviceCalls == calls
  for mode in ["python-drift","engine-drift"] {
   try restore()
   let commands=Probe(engine:engine,rules:rules,mode:mode)
   let reader=SidebarOrganizationRegistryReader(engineURL:engine,rulesURL:rules,commands:commands,engineValidation:{seal.isCurrent()},pythonCandidates:["/python"])
   checks["registry holds "+mode] = await rejects{_ = try await reader.read()}
   try restore()
   let service=SidebarOrganizationService(commands:Probe(engine:engine,rules:rules,mode:mode),homeDirectory:root,temporaryDirectory:root,engineURL:engine,rulesURL:rules,engineValidation:{seal.isCurrent()},pythonCandidates:["/python"])
   checks["classification holds "+mode] = await rejects{_ = try await service.analyze(input)}
  }
  print(String(decoding:try JSONSerialization.data(withJSONObject:checks,options:.sortedKeys),as:UTF8.self))
 }
}
'''


def main():
    with tempfile.TemporaryDirectory(prefix='engine-configuration-contract-') as directory:
        p = Path(directory)
        (p/'Contract.swift').write_text(HARNESS)
        for name, path, pattern in [('CmuxExtensionKit', 'Packages/macOS/CmuxExtensionKit/Sources/CmuxExtensionKit/Sidebar', 'CmuxSidebarContextTag*.swift'), ('CmuxSentryScrubbing', 'Packages/Shared/CmuxSentryTelemetry/Sources/CmuxSentryScrubbing', '*.swift')]:
            sources=list((ROOT/path).glob(pattern))
            if name == 'CmuxExtensionKit':sources.append(ROOT/path/'CmuxSidebarWorkspaceContextProposal.swift')
            subprocess.run(['xcrun', 'swiftc', '-swift-version', '6', '-emit-library', '-emit-module', '-module-name', name, '-emit-module-path', str(p/(name+'.swiftmodule')), '-o', str(p/('lib'+name+'.dylib')), *map(str,sources)], check=True,capture_output=True,text=True,timeout=120)
        foundation=ROOT/'Packages/macOS/CmuxFoundation/Sources/CmuxFoundation/Process'
        (p/'Runner.swift').write_text('import Foundation\npublic struct CommandRunner:CommandRunning {public init(maximumCaptureBytes:Int?=nil){}\npublic func run(directory:String,executable:String,arguments:[String],timeout:TimeInterval?) async->CommandResult{fatalError("inject test seam")}}\n')
        subprocess.run(['xcrun', 'swiftc', '-swift-version', '6', '-emit-library', '-emit-module', '-module-name', 'CmuxFoundation', '-emit-module-path', str(p/'CmuxFoundation.swiftmodule'), '-o', str(p/'libCmuxFoundation.dylib'), str(foundation/'CommandRunning.swift'),str(foundation/'CommandResult.swift'),str(p/'Runner.swift')],check=True,capture_output=True,text=True,timeout=120)
        sources=['SidebarOrganizationEngineConfiguration.swift','SidebarOrganizationRegistryReader.swift','SidebarOrganizationInput.swift','SidebarOrganizationOutput.swift','SidebarOrganizationContextReader.swift','SidebarOrganizationAnalyzing.swift','SidebarOrganizationService.swift']
        result = subprocess.run(['xcrun', 'swiftc', '-swift-version', '6',
                                 '-I',str(p),'-L',str(p),'-lCmuxFoundation','-lCmuxSentryScrubbing','-lCmuxExtensionKit','-Xlinker','-rpath','-Xlinker',str(p),
                                 *[str(ROOT/'Sources'/n) for n in sources],
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
        return 0 if len(checks) == 20 and all(checks.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
