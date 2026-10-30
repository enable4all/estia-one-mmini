import AppKit
import Darwin
import SetupWindowCore

// The Estia Setup window. setup (Terminal) runs it at the desk with its page folder and page.json's sha256, writes
// one JSON line per message to its stdin and reads its answers from its stdout (SetupWindowCore/Message.swift).
// Before anything shows, page.json and every page file are checked; a page that does not check out opens nothing.
// stdout carries only protocol lines: nothing else is ever printed there.

signal(SIGPIPE, SIG_IGN)  // setup gone: a write fails, the window says setup ended

guard let arguments = Arguments.parse(Array(CommandLine.arguments.dropFirst())) else {
    let usage = "usage: estia-setup --page <folder> --manifest <sha256> [--terminal-app <bundle id>] [--check]\n"
    try? FileHandle.standardError.write(contentsOf: Data(usage.utf8))
    exit(2)
}
guard let server = checkedPage(arguments) else { exit(1) }
if arguments.check { exit(0) }

let app = NSApplication.shared
let delegate = AppDelegate(arguments: arguments, server: server)
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
