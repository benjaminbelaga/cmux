import Foundation
import Testing
@testable import CmuxSettings

struct CmuxYOYAKUConfigLocationTests {
    @Test func theCanaryCannotReadOrWriteTheOfficialJSONConfig() {
        let home = URL(fileURLWithPath: "/fixture")
        let official = CmuxConfigLocation(home: home, bundleIdentifier: "com.cmuxterm.app")
        let canary = CmuxConfigLocation(home: home, bundleIdentifier: "com.cmuxterm.app.debug.yoyaku.lordsquire")
        #expect(official.userConfigFile.path == "/fixture/.config/cmux/cmux.json")
        #expect(canary.userConfigFile.path == "/fixture/.config/cmux-yoyaku/cmux.json")
        #expect(canary.legacyFallbackFile.path == "/fixture/.config/cmux-yoyaku/settings.json")
        #expect(canary.userConfigFile != official.userConfigFile)
    }
}
