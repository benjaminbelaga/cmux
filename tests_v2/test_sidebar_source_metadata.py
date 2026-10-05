#!/usr/bin/env python3
"""Compile and exercise the exact typed native metadata without app launches."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'Packages/macOS/CmuxExtensionKit/Sources/CmuxExtensionKit/Sidebar/CmuxSidebarSourceMetadata.swift'
HARNESS = r'''import Foundation
let surface = UUID(uuidString: "11111111-1111-1111-1111-111111111111")!
let other = UUID(uuidString: "22222222-2222-2222-2222-222222222222")!
let now = Date(timeIntervalSince1970: 1234)
let ref = CmuxSidebarSourceReference(accountRef: "MBX-" + String(repeating: "a", count: 64),
    directoryUserId: "123456789", resourceId: "GTK-" + String(repeating: "b", count: 64),
    evidenceFingerprint: String(repeating: "c", count: 64))
let encoder = JSONEncoder(); encoder.dateEncodingStrategy = .iso8601
let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
var result: [String: Bool] = [:]
let data = try encoder.encode(ref)
result["roundTrip"] = try decoder.decode(CmuxSidebarSourceReference.self, from: data) == ref
var object = try JSONSerialization.jsonObject(with: data) as! [String: Any]
object["url"] = "https://mail.google.com"
result["extraUrlRejected"] = (try? decoder.decode(CmuxSidebarSourceReference.self, from: JSONSerialization.data(withJSONObject: object))) == nil
object.removeValue(forKey: "url"); object["accountRef"] = "wrong@example.invalid"
result["emailAccountRejected"] = (try? decoder.decode(CmuxSidebarSourceReference.self, from: JSONSerialization.data(withJSONObject: object))) == nil
object["accountRef"] = ref.accountRef; object["resourceId"] = "arbitrary-resource"
result["unscopedResourceRejected"] = (try? decoder.decode(CmuxSidebarSourceReference.self, from: JSONSerialization.data(withJSONObject: object))) == nil
let observed = CmuxSidebarServiceObservation.browser(url: URL(string: "https://mail.google.com/mail/u/0/#inbox"), surfaceId: surface, observedAt: now)!
result["observedBrowserOnly"] = observed.isCurrent(for: surface, now: now)
result["noHostSuffixInference"] = CmuxSidebarServiceObservation.browser(url: URL(string: "https://mail.google.com.attacker.invalid"), surfaceId: surface, observedAt: now) == nil
result["noEmailTextInference"] = CmuxSidebarServiceObservation.browser(url: URL(string: "admin@example.invalid"), surfaceId: surface, observedAt: now) == nil
result["noUserInfoOrigin"] = CmuxSidebarServiceObservation.browser(url: URL(string: "https://person:password@mail.google.com"), surfaceId: surface, observedAt: now) == nil
result["exactSurface"] = !observed.isCurrent(for: other, now: now)
result["staleOrFutureRejected"] = !observed.isCurrent(for: surface, now: now.addingTimeInterval(13)) && !observed.isCurrent(for: surface, now: now.addingTimeInterval(-1))
let old = try decoder.decode(CmuxSidebarSourceMetadata.self, from: Data("{}".utf8))
result["oldPayloadCompatible"] = old.sourceReferences == nil && old.serviceObservations == nil && old.sourceRequestBindings == nil
let binding = CmuxSidebarSourceRequestBinding(requestId: "CEO-" + String(repeating: "a", count: 32), sourceReferenceFingerprint: ref.fingerprint!, attachedRevision: 7)
result["canonicalRequestIdOnly"] = !CmuxSidebarSourceRequestBinding(requestId: "CEO-canonical-request", sourceReferenceFingerprint: ref.fingerprint!, attachedRevision: 7).isStructurallyValid && !CmuxSidebarSourceRequestBinding(requestId: "CEO-" + String(repeating: "A", count: 32), sourceReferenceFingerprint: ref.fingerprint!, attachedRevision: 7).isStructurallyValid
let valid = CmuxSidebarSourceMetadata(sourceReferences: [ref], serviceObservations: [observed], sourceRequestBindings: [binding])
result["validBoundMapping"] = valid.isStructurallyValid
result["duplicateOrUnboundMappingRejected"] = !CmuxSidebarSourceMetadata(sourceReferences: [ref], sourceRequestBindings: [binding,binding]).isStructurallyValid && !CmuxSidebarSourceMetadata(sourceRequestBindings: [binding]).isStructurallyValid
let excess = CmuxSidebarSourceMetadata(sourceReferences: Array(repeating: ref, count: 33))
result["bounded32"] = (try? decoder.decode(CmuxSidebarSourceMetadata.self, from: encoder.encode(excess))) == nil
print(String(decoding: try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]), as: UTF8.self))
'''

class SourceMetadataContractTests(unittest.TestCase):
    def test_native_metadata_origin_bounds_identity_and_compatibility(self):
        with tempfile.TemporaryDirectory(prefix='sidebar-source-contract-') as directory:
            root=Path(directory);harness=root/'main.swift';harness.write_text(HARNESS)
            binary=root/'contract'
            subprocess.run(['swiftc', '-swift-version', '6', str(SOURCE), str(harness), '-o', str(binary)], check=True, capture_output=True, text=True, timeout=120)
            result=subprocess.run([str(binary)],check=True,capture_output=True,text=True,timeout=10)
            values=json.loads(result.stdout)
            self.assertEqual(len(values),15)
            for name,passed in values.items():
                with self.subTest(name=name): self.assertTrue(passed)

if __name__=='__main__':unittest.main()
