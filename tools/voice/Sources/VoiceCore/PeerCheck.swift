import Darwin
import Foundation

/// Who holds the other end of a connection to the service (owner, 25 Oct: her voice service answers only programs
/// running as her own macOS account). The mirror of the bridge's `voice.peer_is_ours`, which runs lsof as her: macOS
/// shows a program that is not root the open connections of its own account's programs only (libproc, as lsof reads
/// them), so the connection is looked up among the programs of the service's own account only. One that none of them
/// holds (another account's program, root's included, or one already gone) is refused before anything it sends is read.
/// A holder of another account is invisible to her: a connection a program of hers shares with one (handed over) counts
/// as hers, as only a program of hers can share it. The service's own end of it must be its one live end (`liveEnd`).
/// No allowance: a caller of another account runs as her instead.
public enum PeerCheck {
    /// One end of a loopback connection: the IPv4 address in network order and the port.
    public struct Endpoint: Hashable, Sendable {
        public let address: [UInt8]
        public let port: UInt16

        public init(address: [UInt8], port: UInt16) {
            self.address = address
            self.port = port
        }
    }

    /// A TCP connection a program holds: `local` its own end, `remote` the end it reaches.
    public struct Holder: Equatable, Sendable {
        public let pid: pid_t
        public let uid: uid_t
        public let local: Endpoint
        public let remote: Endpoint
        public let state: Int32  // its TCP state (TSI_S_ESTABLISHED, …)

        public init(pid: pid_t, uid: uid_t, local: Endpoint, remote: Endpoint, state: Int32 = TSI_S_ESTABLISHED) {
            (self.pid, self.uid, self.local, self.remote, self.state) = (pid, uid, local, remote, state)
        }
    }

    /// The refusals' log lines (review R1-F1: one line each let a program calling in a loop fill her voice log, which is
    /// emptied at 10 MB): a refusal's own line at once, then the next minute's refusals counted into one line at its
    /// end, and so on while they go on; after a minute without one, the next refusal writes its own line again.
    public struct RefusalLog: Sendable {
        public static let interval: TimeInterval = 60
        private var quiet = false  // a line went out less than an interval ago: refusals are counted
        private var held = 0

        public init() {}

        /// The line to write now for a refusal; nil when it is counted instead. After a line, the caller asks `due`
        /// once the interval is over.
        public mutating func refused(_ verdict: Verdict) -> String? {
            if quiet {
                held += 1
                return nil
            }
            quiet = true
            return PeerCheck.refusal(verdict)
        }

        /// At the end of an interval: the count line (another interval starts), or nil when none came.
        public mutating func due() -> String? {
            guard held > 0 else {
                quiet = false
                return nil
            }
            defer { held = 0 }
            return "refused \(held) more connection\(held == 1 ? "" : "s") in the last minute"
        }
    }

    /// Whether `end` is a program's live end of the connection from `peer` to `service`: a closed one (reset, kept open
    /// by its program) holds nothing.
    static func holds(_ end: Holder, peer: Endpoint, service: Endpoint) -> Bool {
        end.local == peer && end.remote == service && end.state != TSI_S_CLOSED
    }

    /// Whether the service holds exactly one end of the connection from `peer` (its local end `service`, reaching
    /// `peer`) and it is still connected: established, or the caller has only stopped sending (CLOSE_WAIT). Review
    /// R2-F2, measured on macOS 27: a caller that aborts its connection (a reset) frees its port at once, while the
    /// service's end stays open, closed, with the request still readable; a program of hers can take the port before
    /// the check. Then the service holds a closed end, or two ends of that address pair: refused.
    static func liveEnd(peer: Endpoint, service: Endpoint, ends: [Holder]) -> Bool {
        let mine = ends.filter { $0.local == service && $0.remote == peer }
        guard mine.count == 1, let end = mine.first else { return false }
        return end.state == TSI_S_ESTABLISHED || end.state == TSI_S__CLOSE_WAIT
    }

    public enum Verdict: Equatable, Sendable {
        case hers(pid_t)  // a program of the service's own account holds it: answered
        case otherAccount(uid_t)  // a holder runs as another account: refused
        case unknown  // no program of the service's account holds it live (another account's, gone or reset): refused
    }

    /// What a refused connection gets, before anything it sends is read.
    public static let refused = HTTP.json(403, ["error": "other_account"])

    /// The decision for the connection from `peer` to the service at `service`: hers only when a program holds its
    /// other end (its local end `peer`, reaching `service`, not closed) and every such holder runs as `own`.
    public static func verdict(peer: Endpoint, service: Endpoint, holders: [Holder], own: uid_t) -> Verdict {
        let ends = holders.filter { holds($0, peer: peer, service: service) }
        guard let first = ends.first else { return .unknown }
        if let other = ends.first(where: { $0.uid != own }) { return .otherAccount(other.uid) }
        return .hers(first.pid)
    }

    /// The log line of a refusal: metadata only (no address, no request content). "Hers": the owner reads it from his
    /// own account (`estia logs voice`), where "this account" read as his.
    public static func refusal(_ verdict: Verdict) -> String {
        switch verdict {
        case .otherAccount(let uid): return "refused a connection from another account (uid \(uid))"
        case .unknown, .hers:
            return "refused a connection: no program of hers holds it (another account's program, or one already gone)"
        }
    }

    /// The verdict from this Mac's kernel, for the service running in this process: `first` (the program found last,
    /// usually the bridge) is asked alone first, and only when it does not hold the other end are the programs of
    /// account `own` listed and asked, until one does. Then the service's own end must be the connection's one live end
    /// (`liveEnd`), read after the holder: a reset before that read shows there.
    public static func look(peer: Endpoint, service: Endpoint, own: uid_t, first: pid_t? = nil) -> Verdict {
        look(
            peer: peer, service: service, own: own, first: first, server: getpid(), programs: programs(of:),
            connections: connections(of:))
    }

    /// `look` with the kernel's two reads given: `programs` (an account's pids) and `connections` (a pid's TCP ends).
    static func look(
        peer: Endpoint, service: Endpoint, own: uid_t, first: pid_t?, server: pid_t,
        programs: (uid_t) -> [pid_t], connections: (pid_t) -> [Holder]
    ) -> Verdict {
        let found = whoHolds(
            peer: peer, service: service, own: own, first: first, programs: programs, connections: connections)
        guard case .hers = found else { return found }
        return liveEnd(peer: peer, service: service, ends: connections(server)) ? found : .unknown
    }

    private static func whoHolds(
        peer: Endpoint, service: Endpoint, own: uid_t, first: pid_t?,
        programs: (uid_t) -> [pid_t], connections: (pid_t) -> [Holder]
    ) -> Verdict {
        func held(by pid: pid_t) -> Verdict? {
            let ends = connections(pid).filter { holds($0, peer: peer, service: service) }
            return ends.isEmpty ? nil : verdict(peer: peer, service: service, holders: ends, own: own)
        }
        if let first, let found = held(by: first) { return found }
        for pid in programs(own) where pid != first {
            if let found = held(by: pid) { return found }
        }
        return .unknown
    }

    /// The pids of the programs whose effective account is `uid` (what macOS compares when it shows their files).
    static func programs(of uid: uid_t) -> [pid_t] {
        let needed = proc_listpids(UInt32(PROC_UID_ONLY), uid, nil, 0)
        guard needed > 0 else { return [] }
        var pids = [pid_t](repeating: 0, count: Int(needed) / MemoryLayout<pid_t>.stride + 64)  // room for new ones
        let got = pids.withUnsafeMutableBytes {
            proc_listpids(UInt32(PROC_UID_ONLY), uid, $0.baseAddress, Int32($0.count))
        }
        guard got > 0 else { return [] }
        return pids.prefix(Int(got) / MemoryLayout<pid_t>.stride).filter { $0 > 0 }
    }

    /// A program's effective account; nil when it is gone.
    static func account(of pid: pid_t) -> uid_t? {
        var info = proc_bsdshortinfo()
        let size = Int32(MemoryLayout<proc_bsdshortinfo>.size)
        guard proc_pidinfo(pid, PROC_PIDT_SHORTBSDINFO, 0, &info, size) == size else { return nil }
        return info.pbsi_uid
    }

    /// A program's IPv4 TCP connections (an IPv6 socket's mapped IPv4 ones too), and its reset ones: closed, kept open
    /// by the program, with no address family flag left but their IPv4 ends still there (measured on macOS 27); none
    /// when macOS does not show its files (another account's program, for an account that is not root) or it is gone.
    static func connections(of pid: pid_t) -> [Holder] {
        guard let uid = account(of: pid) else { return [] }
        let needed = proc_pidinfo(pid, PROC_PIDLISTFDS, 0, nil, 0)
        guard needed > 0 else { return [] }
        let stride = MemoryLayout<proc_fdinfo>.stride
        var fds = [proc_fdinfo](repeating: proc_fdinfo(), count: Int(needed) / stride + 16)
        let got = fds.withUnsafeMutableBytes { proc_pidinfo(pid, PROC_PIDLISTFDS, 0, $0.baseAddress, Int32($0.count)) }
        guard got > 0 else { return [] }
        return fds.prefix(Int(got) / stride).compactMap { fd in
            guard fd.proc_fdtype == UInt32(PROX_FDTYPE_SOCKET) else { return nil }
            var info = socket_fdinfo()
            let size = Int32(MemoryLayout<socket_fdinfo>.size)
            guard proc_pidfdinfo(pid, fd.proc_fd, PROC_PIDFDSOCKETINFO, &info, size) == size,
                info.psi.soi_kind == Int32(SOCKINFO_TCP)
            else { return nil }
            let ends = info.psi.soi_proto.pri_tcp.tcpsi_ini
            let reset = ends.insi_vflag == 0 && info.psi.soi_proto.pri_tcp.tcpsi_state == TSI_S_CLOSED
            guard ends.insi_vflag & UInt8(INI_IPV4) != 0 || reset else { return nil }
            return Holder(
                pid: pid, uid: uid,
                local: endpoint(ends.insi_laddr.ina_46.i46a_addr4, ends.insi_lport),
                remote: endpoint(ends.insi_faddr.ina_46.i46a_addr4, ends.insi_fport),
                state: info.psi.soi_proto.pri_tcp.tcpsi_state)
        }
    }

    /// libproc's address (network order) and port (a network-order 16-bit value in an int).
    private static func endpoint(_ address: in_addr, _ port: Int32) -> Endpoint {
        Endpoint(
            address: withUnsafeBytes(of: address.s_addr) { Array($0) },
            port: UInt16(bigEndian: UInt16(truncatingIfNeeded: port)))
    }
}
