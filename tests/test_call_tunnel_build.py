"""tools/call-tunnel/build.sh: the call helper is built again when its sources change (second deep scan, signal-06).
build.sh writes a stamp beside the helper it built (its PR head, its RingRTC pin and local.patch's sha256); setup and
doctor compare it with the checkout's or the release's own sources (`config.tunnel_stamp`). Run with stub git, brew
and the upstream's own build script in a temporary folder: never the network, never cargo."""

import subprocess
from pathlib import Path

from local_assistant import config

BUILD = Path(__file__).resolve().parents[1] / "tools" / "call-tunnel" / "build.sh"


def stub(folder: Path, name: str, body: str) -> None:
    path = folder / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


def test_the_stamp_names_the_sources_by_their_pins_and_the_patch_by_its_sha256() -> None:
    stamp = config.tunnel_stamp()
    assert stamp is not None
    lines = stamp.splitlines()
    assert [line.split("=", 1)[0] for line in lines] == ["pr5_head", "ringrtc", "local_patch"]
    script = BUILD.read_text()
    assert f"PR5_HEAD={lines[0].split('=', 1)[1]}" in script and f"RINGRTC={lines[1].split('=', 1)[1]}" in script


def test_build_sh_resets_its_clone_applies_the_patch_and_writes_the_stamp_beside_the_helper(tmp_path: Path) -> None:
    """A later Estia changes local.patch: the clone still holds the old patch, so build.sh resets it first (a hard
    reset of its own clone), applies the patch and, once built, writes the stamp setup and doctor read."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    log = tmp_path / "git.log"
    ringrtc = next(line for line in BUILD.read_text().splitlines() if line.startswith("RINGRTC=")).split("=")[1][:40]
    stub(
        stubs,
        "git",
        f'echo "$*" >> {log}\ncase "$*" in *"rev-parse HEAD"*) echo ' + ringrtc + " ;; esac\nexit 0",
    )
    stub(stubs, "brew", f"echo {tmp_path / 'brew'}")
    dest = tmp_path / "signal-call-tunnel"
    (dest / ".git").mkdir(parents=True)
    stub(  # the upstream's own build script: leaves the helper where cargo would
        dest,
        "build.sh",
        "mkdir -p signal-call-tunnel/target/release && echo helper > signal-call-tunnel/target/release/signal-call-tunnel",
    )
    helper = dest / "signal-call-tunnel/target/release/signal-call-tunnel"
    stamp = helper.with_name(helper.name + ".stamp")
    helper.parent.mkdir(parents=True)
    stamp.write_text("pr5_head=old\n")  # from a build before: gone while the new one builds
    done = subprocess.run(
        ["/bin/sh", str(BUILD), str(dest)],
        env={"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert stamp.read_text() == config.tunnel_stamp()
    calls = log.read_text().splitlines()
    reset = next(i for i, line in enumerate(calls) if line.startswith("reset"))
    apply = next(i for i, line in enumerate(calls) if line.startswith("apply") and "--check" not in line)
    assert calls[reset] == "reset -q --hard" and reset < apply, calls
    assert not config.tunnel_stamp_differs(helper)
    stamp.write_text("pr5_head=old\n")
    assert config.tunnel_stamp_differs(helper)
    stamp.unlink()
    assert config.tunnel_stamp_differs(helper)  # built before Estia noted its sources
    assert not config.tunnel_stamp_differs(tmp_path / "none")  # no helper: nothing to compare
