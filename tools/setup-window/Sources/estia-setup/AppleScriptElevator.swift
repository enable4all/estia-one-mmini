import Foundation
import SetupWindowCore

/// Admin rights through macOS's own box: AppleScript's `do shell script … with administrator privileges`, run inside
/// this app (no helper, no signing, no Automation permission: an Apple Event to the app itself, never to another
/// app). macOS shows its standard box, "Estia Setup wants to make changes.", with setup's reason as its prompt; the
/// password goes only to macOS, never through the page, the app or setup. The one script (AdminCore.appleScript) is
/// compiled once and never changes, so macOS's 5 minutes without asking again hold for it (TN2065); its handler gets
/// the command's words as a list, each quoted by AppleScript itself: no word is ever shell or AppleScript text. It
/// runs on the main thread (NSAppleScript's), and only until the wrapper has started the command in the background:
/// the box is the only wait. The main thread draws nothing while the box asks, so the box comes a moment after the
/// request: the page has drawn its "In macOS's own box" line by then (review W09; 0.9.9: the box off the main
/// thread). This is the app's only AppleScript.
final class AppleScriptElevator: Elevator {
    private var script: NSAppleScript?

    func elevate(words: [String], prompt: String, done: @escaping (Elevated) -> Void) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { done(self.run(words, prompt)) }
    }

    private func run(_ words: [String], _ prompt: String) -> Elevated {
        guard let script = compiled() else { return .failed(AdminCore.notRun) }
        let list = NSAppleEventDescriptor.list()
        for (index, word) in words.enumerated() { list.insert(NSAppleEventDescriptor(string: word), at: index + 1) }
        let parameters = NSAppleEventDescriptor.list()
        parameters.insert(list, at: 1)
        parameters.insert(NSAppleEventDescriptor(string: prompt), at: 2)
        let event = NSAppleEventDescriptor.appleEvent(
            withEventClass: code("ascr"), eventID: code("psbr"),  // kASAppleScriptSuite, kASSubroutineEvent
            targetDescriptor: NSAppleEventDescriptor.currentProcess(), returnID: AEReturnID(-1), transactionID: 0)
        event.setDescriptor(NSAppleEventDescriptor(string: "runasadmin"), forKeyword: code("snam"))  // its handler
        event.setDescriptor(parameters, forKeyword: code("----"))  // keyDirectObject: its parameters
        var error: NSDictionary?
        _ = script.executeAppleEvent(event, error: &error)
        guard let error else { return .started }
        return AdminCore.elevated(errorNumber: (error[NSAppleScript.errorNumber] as? Int) ?? AdminCore.notRun)
    }

    private func compiled() -> NSAppleScript? {
        if let script { return script }
        guard let made = NSAppleScript(source: AdminCore.appleScript(base: AdminCore.base)) else { return nil }
        var error: NSDictionary?
        guard made.compileAndReturnError(&error) else { return nil }
        script = made
        return made
    }

    private func code(_ text: String) -> UInt32 {
        text.utf8.reduce(0) { $0 << 8 | UInt32($1) }
    }
}
