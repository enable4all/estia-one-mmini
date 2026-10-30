import Foundation
import Testing

@testable import VoiceCore

/// Owner, 25 Oct: her voice service answers only programs running as her own macOS account (the Tiffany line's review
/// of 25 Oct, J2: any program on the Mac could have it transcribe audio, speak any text in her voices and download
/// pinned models into her home). The mirror of the bridge's `voice.peer_is_ours`: the program that holds the other end
/// of a connection is looked up among her account's own programs, which are the only ones macOS shows her.
@Suite struct PeerChecking {
    private let service = PeerCheck.Endpoint(address: [127, 0, 0, 1], port: 8150)
    private let peer = PeerCheck.Endpoint(address: [127, 0, 0, 1], port: 50_123)
    private let hers: uid_t = 262
    private let yours: uid_t = 501

    private func holder(
        _ pid: pid_t, _ uid: uid_t, local: PeerCheck.Endpoint, remote: PeerCheck.Endpoint,
        state: Int32 = TSI_S_ESTABLISHED
    ) -> PeerCheck.Holder {
        PeerCheck.Holder(pid: pid, uid: uid, local: local, remote: remote, state: state)
    }

    @Test func aConnectionAProgramOfHerOwnAccountHoldsIsAnswered() {
        let bridge = holder(41, hers, local: peer, remote: service)
        #expect(PeerCheck.verdict(peer: peer, service: service, holders: [bridge], own: hers) == .hers(41))
    }

    @Test func aConnectionAProgramOfAnotherAccountHoldsIsRefused() {
        let yourProgram = holder(77, yours, local: peer, remote: service)
        let verdict = PeerCheck.verdict(peer: peer, service: service, holders: [yourProgram], own: hers)
        #expect(verdict == .otherAccount(yours))
        let root = holder(1, 0, local: peer, remote: service)  // root too: no allowance
        #expect(PeerCheck.verdict(peer: peer, service: service, holders: [root], own: hers) == .otherAccount(0))
    }

    @Test func theRuleRefusesAHolderOfAnotherAccountEvenBesideOneOfHers() {
        // the rule alone (`verdict`): every holder it is given must be hers. The live lookup is given only her
        // programs' holders (macOS hides another account's from her), so there a connection one of hers shares with
        // another account's program counts as hers: only a program of hers can hand it over
        let both = [holder(41, hers, local: peer, remote: service), holder(77, yours, local: peer, remote: service)]
        #expect(PeerCheck.verdict(peer: peer, service: service, holders: both, own: hers) == .otherAccount(yours))
    }

    @Test func aConnectionNoProgramOfHersHoldsIsRefused() {
        #expect(PeerCheck.verdict(peer: peer, service: service, holders: [], own: hers) == .unknown)
        // the service's own end of it (its local end is the service), another port and another address are not it
        let own = holder(9, hers, local: service, remote: peer)
        let otherPort = holder(41, hers, local: .init(address: [127, 0, 0, 1], port: 50_124), remote: service)
        let otherService = holder(41, hers, local: peer, remote: .init(address: [127, 0, 0, 1], port: 8151))
        let elsewhere = holder(41, hers, local: peer, remote: .init(address: [192, 0, 2, 7], port: 8150))
        for wrong in [own, otherPort, otherService, elsewhere] {
            #expect(PeerCheck.verdict(peer: peer, service: service, holders: [wrong], own: hers) == .unknown)
        }
    }

    @Test func aClosedEndOfHersHoldsNothing() {
        // a reset end stays open in its program, closed: it holds no connection, so a live one of another account's
        // program that took its port since is not made hers by it
        let dead = holder(41, hers, local: peer, remote: service, state: TSI_S_CLOSED)
        #expect(PeerCheck.verdict(peer: peer, service: service, holders: [dead], own: hers) == .unknown)
        let stoppedSending = holder(41, hers, local: peer, remote: service, state: TSI_S_FIN_WAIT_2)
        #expect(PeerCheck.verdict(peer: peer, service: service, holders: [stoppedSending], own: hers) == .hers(41))
    }

    @Test func eachRefusalIsOneLogLineWithNoRequestContent() {
        let lines = [PeerCheck.refusal(.unknown), PeerCheck.refusal(.otherAccount(yours))]
        // "hers", never "this account": the owner reads it from his own account (`estia logs voice`)
        #expect(lines[0] == "refused a connection: no program of hers holds it (another account's program, or one already gone)")
        #expect(lines[1] == "refused a connection from another account (uid 501)")
        #expect(lines.allSatisfy { !$0.contains("\n") })
    }

    @Test func theServicesOwnEndMustBeItsOneLiveEnd() {
        // review R2-F2: a caller that resets its connection (an abort) frees its port at once; a program of hers that
        // takes that port before the check makes the dead connection look hers. The service's own end tells them
        // apart: one end of the connection, still connected
        let live = holder(9, hers, local: service, remote: peer)
        #expect(PeerCheck.liveEnd(peer: peer, service: service, ends: [live]))
        let stoppedSending = holder(9, hers, local: service, remote: peer, state: TSI_S__CLOSE_WAIT)
        #expect(PeerCheck.liveEnd(peer: peer, service: service, ends: [stoppedSending]))
        let reset = holder(9, hers, local: service, remote: peer, state: TSI_S_CLOSED)
        #expect(!PeerCheck.liveEnd(peer: peer, service: service, ends: [reset]))
        #expect(!PeerCheck.liveEnd(peer: peer, service: service, ends: [reset, live]))  // the port taken again since
        #expect(!PeerCheck.liveEnd(peer: peer, service: service, ends: []))
        // the caller's end, and the service's ends of other connections, are not it
        let callers = holder(41, hers, local: peer, remote: service)
        let another = holder(9, hers, local: service, remote: .init(address: [127, 0, 0, 1], port: 50_124))
        #expect(!PeerCheck.liveEnd(peer: peer, service: service, ends: [callers, another]))
        #expect(PeerCheck.liveEnd(peer: peer, service: service, ends: [callers, another, live]))
    }

    @Test func theProgramFoundLastIsAskedAloneBeforeHerProgramsAreListed() {
        // review R1-F2: the bridge (found last) answers at once, without listing every program of hers
        var listed = 0
        let bridge = holder(41, hers, local: peer, remote: service)
        let own = holder(9, hers, local: service, remote: peer)
        let kernel: (pid_t) -> [PeerCheck.Holder] = { pid in pid == 41 ? [bridge] : pid == 9 ? [own] : [] }
        let found = PeerCheck.look(
            peer: peer, service: service, own: hers, first: 41, server: 9,
            programs: { _ in listed += 1; return [7, 41] }, connections: kernel)
        #expect(found == .hers(41) && listed == 0)
        // another program of hers holds it: then they are listed, once
        let curl = holder(55, hers, local: peer, remote: service)
        let missed = PeerCheck.look(
            peer: peer, service: service, own: hers, first: 41, server: 9,
            programs: { _ in listed += 1; return [41, 55] },
            connections: { pid in pid == 55 ? [curl] : pid == 9 ? [own] : [] })
        #expect(missed == .hers(55) && listed == 1)
    }

    @Test func aConnectionOfHersIsRefusedWhenTheServicesEndOfItIsNotLive() {
        let bridge = holder(41, hers, local: peer, remote: service)
        let reset = holder(9, hers, local: service, remote: peer, state: TSI_S_CLOSED)
        let found = PeerCheck.look(
            peer: peer, service: service, own: hers, first: 41, server: 9,
            programs: { _ in [41] }, connections: { pid in pid == 41 ? [bridge] : pid == 9 ? [reset] : [] })
        #expect(found == .unknown)
    }

    @Test func theRefusalAnswerIsForbiddenAndNamesWhy() {
        let answer = String(decoding: PeerCheck.refused, as: UTF8.self)
        #expect(answer.hasPrefix("HTTP/1.1 403 Forbidden\r\n"))
        #expect(answer.hasSuffix("{\"error\":\"other_account\"}"))
    }
}

/// Review R1-F1: a stream of refused connections wrote one line each, so her voice log (10 MB) was emptied in hours and
/// the lines doctor sends the owner to were gone. A refusal's own line, then one counted line a minute.
@Suite struct RefusalLogging {
    @Test func aRefusalIsOneLineThenTheNextMinutesAreOneCountedLine() {
        var refusals = PeerCheck.RefusalLog()
        #expect(refusals.refused(.unknown) == PeerCheck.refusal(.unknown))
        for _ in 0..<500 { #expect(refusals.refused(.unknown) == nil) }  // counted, not written
        #expect(refusals.due() == "refused 500 more connections in the last minute")
        #expect(refusals.refused(.unknown) == nil)  // still counted: the count line starts another minute
        #expect(refusals.due() == "refused 1 more connection in the last minute")
        #expect(refusals.due() == nil)  // a minute without one: the next refusal writes its own line at once
        #expect(refusals.refused(.otherAccount(501)) == PeerCheck.refusal(.otherAccount(501)))
        #expect(PeerCheck.RefusalLog.interval == 60)
    }
}

/// The lookup on this Mac's own kernel: a real loopback connection this test process holds.
@Suite struct PeerLookup {
    /// A listening socket on 127.0.0.1, a client connected to it and the accepted end, all of this process.
    private final class Loopback {
        let listener: Int32, accepted: Int32
        private(set) var client: Int32
        let service: PeerCheck.Endpoint, peer: PeerCheck.Endpoint

        init() throws {
            let listening = socket(AF_INET, SOCK_STREAM, 0)
            let size = socklen_t(MemoryLayout<sockaddr_in>.size)
            try Self.check(Self.at(Self.loopback(port: 0)) { bind(listening, $0, size) })  // any free port
            try Self.check(listen(listening, 1))
            let service = try Self.name(listening)
            let connecting = socket(AF_INET, SOCK_STREAM, 0)
            try Self.check(Self.at(Self.loopback(port: service.port)) { connect(connecting, $0, size) })
            let taken = accept(listening, nil, nil)
            try Self.check(taken)
            (listener, client, accepted) = (listening, connecting, taken)
            (self.service, peer) = (service, try Self.name(connecting))
        }

        static func loopback(port: UInt16) -> sockaddr_in {
            var address = sockaddr_in()
            address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
            address.sin_family = sa_family_t(AF_INET)
            address.sin_addr.s_addr = inet_addr("127.0.0.1")
            address.sin_port = port.bigEndian
            return address
        }

        /// `body` with `address` as the sockaddr bind and connect take.
        static func at(_ address: sockaddr_in, _ body: (UnsafePointer<sockaddr>) -> Int32) -> Int32 {
            withUnsafePointer(to: address) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1, body) }
        }

        deinit { [client, accepted, listener].filter { $0 >= 0 }.forEach { _ = close($0) } }

        /// The client's program lets the connection go: only the service's own end of it is left.
        func closeClient() {
            close(client)
            client = -1
        }

        /// The client aborts the connection (a reset, SO_LINGER 0): its port is free again at once.
        func abortClient() {
            var abort = linger(l_onoff: 1, l_linger: 0)
            setsockopt(client, SOL_SOCKET, SO_LINGER, &abort, socklen_t(MemoryLayout<linger>.size))
            closeClient()
        }

        static func check(_ result: Int32) throws {
            if result < 0 { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        }

        static func name(_ fd: Int32) throws -> PeerCheck.Endpoint {
            var address = sockaddr_in()
            var size = socklen_t(MemoryLayout<sockaddr_in>.size)
            let named = withUnsafeMutablePointer(to: &address) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(fd, $0, &size) }
            }
            try check(named)
            let bytes = withUnsafeBytes(of: address.sin_addr.s_addr) { Array($0) }
            return PeerCheck.Endpoint(address: bytes, port: UInt16(bigEndian: address.sin_port))
        }
    }

    @Test func aConnectionThisAccountsProgramHoldsIsFoundAsHers() throws {
        let line = try Loopback()
        #expect(line.peer.address == [127, 0, 0, 1] && line.peer.port != line.service.port)
        #expect(PeerCheck.look(peer: line.peer, service: line.service, own: geteuid()) == .hers(getpid()))
        // looked at first (the last program found): the same answer at once
        let first = PeerCheck.look(peer: line.peer, service: line.service, own: geteuid(), first: getpid())
        #expect(first == .hers(getpid()))
    }

    @Test func forAServiceOfAnotherAccountThisProgramIsNotHers() throws {
        let line = try Loopback()
        let another: uid_t = geteuid() == 1 ? 2 : 1  // macOS's daemon account (or bin): never this test's
        // that account's programs: macOS shows none of their connections to this one, so none holds it
        #expect(PeerCheck.look(peer: line.peer, service: line.service, own: another) == .unknown)
        // this program, looked at first, holds it, and runs as another account than the service's
        let first = PeerCheck.look(peer: line.peer, service: line.service, own: another, first: getpid())
        #expect(first == .otherAccount(geteuid()))
    }

    @Test func aConnectionThatIsGoneIsRefused() throws {
        let line = try Loopback()
        line.closeClient()  // the service's own end (local: the service, remote: the peer) never counts as the peer
        #expect(PeerCheck.look(peer: line.peer, service: line.service, own: geteuid()) == .unknown)
        let never = PeerCheck.Endpoint(address: [127, 0, 0, 1], port: 9)  // no program of this account holds it
        #expect(PeerCheck.look(peer: never, service: line.service, own: geteuid()) == .unknown)
    }

    /// A socket of `family` connected to the listener at `service` from 127.0.0.1 (as ::ffff:127.0.0.1 for AF_INET6),
    /// bound to `port` when one is given.
    private static func connected(to service: PeerCheck.Endpoint, family: Int32 = AF_INET, from port: UInt16 = 0) throws
        -> Int32
    {
        let fd = socket(family, SOCK_STREAM, 0)
        try Loopback.check(fd)
        if family == AF_INET6 {
            var address = sockaddr_in6()
            address.sin6_len = UInt8(MemoryLayout<sockaddr_in6>.size)
            address.sin6_family = sa_family_t(AF_INET6)
            address.sin6_port = service.port.bigEndian
            _ = inet_pton(AF_INET6, "::ffff:127.0.0.1", &address.sin6_addr)
            let done = withUnsafePointer(to: address) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in6>.size))
                }
            }
            try Loopback.check(done)
            return fd
        }
        let size = socklen_t(MemoryLayout<sockaddr_in>.size)
        if port != 0 {
            var reuse: Int32 = 1
            setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse, socklen_t(MemoryLayout<Int32>.size))
            try Loopback.check(Loopback.at(Loopback.loopback(port: port)) { bind(fd, $0, size) })
        }
        try Loopback.check(Loopback.at(Loopback.loopback(port: service.port)) { connect(fd, $0, size) })
        return fd
    }

    @Test func aConnectionFromAnIPv6SocketToTheMappedAddressIsFoundAsHers() throws {
        // review R1-F5: a client may use an IPv6 socket for 127.0.0.1 (::ffff:127.0.0.1); libproc shows its IPv4 ends
        let line = try Loopback()
        let client = try Self.connected(to: line.service, family: AF_INET6)
        defer { close(client) }
        let taken = accept(line.listener, nil, nil)
        try Loopback.check(taken)
        defer { close(taken) }
        var address = sockaddr_in6()
        var size = socklen_t(MemoryLayout<sockaddr_in6>.size)
        let named = withUnsafeMutablePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(client, $0, &size) }
        }
        try Loopback.check(named)
        let peer = PeerCheck.Endpoint(address: [127, 0, 0, 1], port: UInt16(bigEndian: address.sin6_port))
        #expect(PeerCheck.look(peer: peer, service: line.service, own: geteuid()) == .hers(getpid()))
    }

    @Test func aResetConnectionWhosePortAProgramOfHersTookIsRefused() throws {
        // review R2-F2, as measured on this Mac (25 Oct): after an abort (RST) the service's end stays open, closed,
        // with its request still readable, and the freed port can be taken at once by another program
        let line = try Loopback()
        let attacker = line.peer
        _ = "GET /health HTTP/1.1\r\n\r\n".withCString { send(line.client, $0, strlen($0), 0) }
        line.abortClient()
        // her program takes the freed port and connects; the service has not accepted it yet
        let hers = try Self.connected(to: line.service, from: attacker.port)
        defer { close(hers) }
        #expect(PeerCheck.look(peer: attacker, service: line.service, own: geteuid()) == .unknown)
        // accepted too: the service then holds two ends of that address pair, one dead
        let taken = accept(line.listener, nil, nil)
        try Loopback.check(taken)
        defer { close(taken) }
        #expect(PeerCheck.look(peer: attacker, service: line.service, own: geteuid()) == .unknown)
    }
}
