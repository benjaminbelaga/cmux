#!/usr/bin/env python3
"""Compile and execute verbatim provider mutation helpers with their actor contract.

The native diagnostics owner is a MainActor fixture. Actual provider helpers,
the real setter body and real isolated UserDefaults mutations are exercised;
this does not launch SwiftUI, ExtensionKit or a native application.
"""
from pathlib import Path
import json
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''import Foundation
enum CmuxSidebarProviderDescriptor { static let defaultWorkspacesID = "fixture.classic" }
@MainActor final class Diagnostics {
    var transitions: [(String, String, String)] = []
    var allOnMainThread = true
    func providerChanged(previous: String, current: String, source: String) {
        transitions.append((previous, current, source))
        allOnMainThread = allOnMainThread && Thread.isMainThread
    }
}
@MainActor final class TerminalController {
    static let shared = TerminalController()
    let sidebarRecoveryDiagnostics = Diagnostics()
}
enum CmuxExtensionSidebarSelection {
CONSTANTS
METHODS
}
@main struct Contract {
    @MainActor static func main() throws {
        typealias Selection = CmuxExtensionSidebarSelection
        var values: [String: Bool] = [:]
        var suites: [String] = []
        let diagnostics = TerminalController.shared.sidebarRecoveryDiagnostics
        func fixture() -> UserDefaults {
            let suite = "cmux-provider-contract-" + UUID().uuidString
            suites.append(suite)
            let defaults = UserDefaults(suiteName: suite)!
            defaults.set(Selection.defaultProviderId, forKey: Selection.defaultsKey)
            diagnostics.transitions.removeAll()
            return defaults
        }
        defer { for suite in suites { UserDefaults.standard.removePersistentDomain(forName: suite) } }
        let bundle = "fr.yoyaku.cortex.sessions"
        let disabled = fixture()
        let disabledResult = Selection.selectCortexSidebar(enabledBundleIDs: [bundle], extensionsEnabled: false, defaults: disabled)
        values["disabled selection makes no mutation"] = !disabledResult && disabled.string(forKey: Selection.defaultsKey) == Selection.defaultProviderId && diagnostics.transitions.isEmpty
        let absent = fixture()
        let absentResult = Selection.selectCortexSidebar(enabledBundleIDs: ["unrelated.sidebar"], extensionsEnabled: true, defaults: absent)
        values["missing Cortex candidate makes no mutation"] = !absentResult && absent.string(forKey: Selection.selectedExtensionBundleIDDefaultsKey) == nil && diagnostics.transitions.isEmpty
        let selected = fixture()
        let selectedResult = Selection.selectCortexSidebar(enabledBundleIDs: [bundle], extensionsEnabled: true, defaults: selected)
        values["explicit selection uses native setter and retained bundle"] = selectedResult && selected.string(forKey: Selection.defaultsKey) == Selection.hostedExtensionsProviderId && selected.string(forKey: Selection.selectedExtensionBundleIDDefaultsKey) == bundle && diagnostics.transitions.count == 1 && diagnostics.transitions[0].0 == Selection.defaultProviderId && diagnostics.transitions[0].1 == Selection.hostedExtensionsProviderId
        let toggled = Selection.toggleCortexSidebar(enabledBundleIDs: [bundle], extensionsEnabled: true, defaults: selected)
        values["active toggle returns to classic through native setter"] = toggled && selected.string(forKey: Selection.defaultsKey) == Selection.defaultProviderId && selected.string(forKey: Selection.selectedExtensionBundleIDDefaultsKey) == bundle && diagnostics.transitions.count == 2 && diagnostics.transitions[1].1 == Selection.defaultProviderId
        let inactive = fixture()
        let inactiveResult = Selection.toggleCortexSidebar(enabledBundleIDs: [bundle], extensionsEnabled: true, defaults: inactive)
        values["inactive toggle selects Cortex through native setter"] = inactiveResult && inactive.string(forKey: Selection.defaultsKey) == Selection.hostedExtensionsProviderId && diagnostics.transitions.count == 1
        let denied = fixture()
        let deniedResult = Selection.toggleCortexSidebar(enabledBundleIDs: [bundle], extensionsEnabled: false, defaults: denied)
        values["disabled toggle makes no mutation"] = !deniedResult && denied.string(forKey: Selection.defaultsKey) == Selection.defaultProviderId && diagnostics.transitions.isEmpty
        let paired = fixture()
        let tag = "fr.yoyaku.cortex.dogfood.provider-test.sessions"
        let host = "com.cmuxterm.app.debug.provider.test"
        let pairedResult = Selection.selectCortexSidebar(enabledBundleIDs: [tag, "fr.yoyaku.cortex.dogfood.other-tag.sessions"], extensionsEnabled: true, defaults: paired, hostBundleID: host)
        values["paired selection rejects a foreign tag"] = pairedResult && paired.string(forKey: Selection.selectedExtensionBundleIDDefaultsKey) == tag && diagnostics.transitions.count == 1
        values["native diagnostics run on the main actor thread"] = diagnostics.allOnMainThread
        print(String(decoding: try JSONSerialization.data(withJSONObject: values, options: .sortedKeys), as: UTF8.self))
    }
}
'''


def actual_method(source, name):
    match = re.search(r'^    (?:(?:@MainActor|@discardableResult)\n    )*static func ' + re.escape(name) + r'\(', source, re.MULTILINE)
    if not match:
        raise ValueError('Missing actual provider helper ' + name)
    opening = source.index('{', match.start())
    depth = 0
    for end in range(opening, len(source)):
        depth += (source[end] == '{') - (source[end] == '}')
        if depth == 0:
            return source[match.start():end + 1]
    raise ValueError('Unbalanced actual provider helper ' + name)


def main():
    source = (ROOT / 'Sources/CmuxExtensionSidebarSelection.swift').read_text()
    names = ['setProviderId', 'isCortexBundle', 'isCortexActive', 'toggleCortexSidebar', 'selectCortexSidebar']
    constants = []
    for name in ['defaultsKey', 'selectedExtensionBundleIDDefaultsKey', 'selectedExtensionNameDefaultsKey', 'defaultProviderId', 'hostedExtensionsProviderId']:
        match = re.search(r'^    static let ' + name + r' = [^\n]+', source, re.MULTILINE)
        if not match:
            raise ValueError('Missing actual provider constant ' + name)
        constants.append(match.group())
    contract = HARNESS.replace('CONSTANTS', '\n'.join(constants)).replace('METHODS', '\n'.join(actual_method(source, name) for name in names))
    with tempfile.TemporaryDirectory(prefix='cmux-provider-selection-') as directory:
        temporary = Path(directory)
        swift = temporary / 'Contract.swift'
        swift.write_text(contract)
        binary = temporary / 'contract'
        compiled = subprocess.run(['xcrun', 'swiftc', '-swift-version', '6', '-strict-concurrency=complete', str(swift), '-o', str(binary)], capture_output=True, text=True, timeout=90)
        if compiled.returncode:
            print(compiled.stderr)
            return 2
        values = json.loads(subprocess.run([str(binary)], capture_output=True, text=True, check=True, timeout=10).stdout)
        for name, passed in values.items():
            print(('PASS ' if passed else 'FAIL ') + name)
        print(json.dumps({'passed': sum(values.values()), 'failed': sum(not value for value in values.values())}))
        return 0 if len(values) == 8 and all(values.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
