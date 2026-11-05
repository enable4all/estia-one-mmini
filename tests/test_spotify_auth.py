import base64
import hashlib
import json
import stat
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from local_assistant.mail_http import HttpResult
from local_assistant.spotify_auth import (
    LIFETIME_SECONDS,
    REDIRECT_URI,
    SCOPES,
    TOKEN_URL,
    SpotifyAuthError,
    SpotifySignInExpired,
    SpotifyTokens,
    authorize_url,
    code_from,
    login,
    pkce_pair,
)
from tests.fakes.fake_http import FakeHttp
from tests.setup_helpers import noted

CLIENT = "0123456789abcdef0123456789abcdef"
NOW = 1_800_000_000.0


def test_pkce_pair_follows_s256() -> None:
    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == digest


def test_authorize_url_asks_for_the_minimal_scopes_with_pkce() -> None:
    url = authorize_url(CLIENT, "challenge", "state123")
    parts = urlsplit(url)
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert parts.netloc == "accounts.spotify.com" and parts.path == "/authorize"
    assert query == {
        "client_id": CLIENT,
        "response_type": "code",
        "redirect_uri": "http://127.0.0.1:8888/callback",
        "code_challenge_method": "S256",
        "code_challenge": "challenge",
        "scope": SCOPES,
        "state": "state123",
    }
    assert query["redirect_uri"] == REDIRECT_URI


def test_code_from_the_pasted_address_checks_the_state() -> None:
    assert code_from("http://127.0.0.1:8888/callback?code=abc&state=s1", "s1") == "abc"
    assert code_from("  http://127.0.0.1:8888/callback?code=abc&state=s1\n", "s1") == "abc"
    with pytest.raises(SpotifyAuthError, match="state"):
        code_from("http://127.0.0.1:8888/callback?code=abc&state=other", "s1")
    with pytest.raises(SpotifyAuthError, match="access_denied"):
        code_from("http://127.0.0.1:8888/callback?error=access_denied&state=s1", "s1")
    with pytest.raises(SpotifyAuthError, match="no code"):
        code_from("http://127.0.0.1:8888/callback?state=s1", "s1")


def tokens(tmp_path: Path, http: FakeHttp, now: float = NOW) -> SpotifyTokens:
    return SpotifyTokens(tmp_path / "data" / "spotify-tokens.json", CLIENT, http=http, clock=lambda: now)


async def test_exchange_saves_a_private_refresh_token_with_its_sign_in_time(tmp_path: Path) -> None:
    http = FakeHttp()
    http.add(TOKEN_URL, 200, {"access_token": "A1", "refresh_token": "R1", "expires_in": 3600, "scope": SCOPES})
    store = tokens(tmp_path, http)
    await store.exchange("CODE", "VERIFIER")
    form = http.requests[0][2]
    assert form["grant_type"] == "authorization_code" and form["code"] == "CODE"
    assert form["code_verifier"] == "VERIFIER" and form["client_id"] == CLIENT and form["redirect_uri"] == REDIRECT_URI
    saved = json.loads(store.path.read_text())
    assert saved["refresh_token"] == "R1" and saved["authorized_at"] == NOW
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700
    assert store.expires_at() == NOW + LIFETIME_SECONDS
    assert await store.access_token() == "A1"  # the exchange's access token is used first


async def test_a_link_at_the_temporary_name_is_never_followed(tmp_path: Path) -> None:
    """A link at spotify-tokens.json.tmp never makes the save write or chmod the file it names (review I37)."""
    http = FakeHttp()
    http.add(TOKEN_URL, 200, {"access_token": "A1", "refresh_token": "R1", "expires_in": 3600, "scope": SCOPES})
    store = tokens(tmp_path, http)
    store.path.parent.mkdir(mode=0o700)
    victim = tmp_path / "authorized_keys"
    victim.write_text("ssh-ed25519 AAAA kept\n")
    victim.chmod(0o644)
    (store.path.parent / "spotify-tokens.json.tmp").symlink_to("../authorized_keys")
    await store.exchange("CODE", "VERIFIER")
    assert victim.read_text() == "ssh-ed25519 AAAA kept\n" and stat.S_IMODE(victim.stat().st_mode) == 0o644
    assert not store.path.is_symlink() and stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert json.loads(store.path.read_text())["refresh_token"] == "R1"


async def test_access_tokens_refresh_and_keep_the_sign_in_time(tmp_path: Path) -> None:
    http = FakeHttp()
    http.add(TOKEN_URL, 200, {"access_token": "A1", "refresh_token": "R1", "expires_in": 3600})
    store = tokens(tmp_path, http)
    await store.exchange("CODE", "V")
    later = tokens(tmp_path, http, now=NOW + 7200)  # a new process, the access token long gone
    http.answers[TOKEN_URL] = [HttpResult(200, {}, {"access_token": "A2", "expires_in": 3600})]
    assert await later.access_token() == "A2"
    assert http.requests[-1][2]["grant_type"] == "refresh_token" and http.requests[-1][2]["refresh_token"] == "R1"
    assert json.loads(later.path.read_text())["refresh_token"] == "R1"  # no new one sent: the old one stays
    http.answers[TOKEN_URL] = [HttpResult(200, {}, {"access_token": "A3", "refresh_token": "R2", "expires_in": 1})]
    later.forget()
    assert await later.access_token() == "A3"
    saved = json.loads(later.path.read_text())
    assert saved["refresh_token"] == "R2" and saved["authorized_at"] == NOW  # rotation keeps the 6-month clock


async def test_an_ended_sign_in_is_told_apart(tmp_path: Path) -> None:
    http = FakeHttp()
    store = tokens(tmp_path, http)
    with pytest.raises(SpotifySignInExpired):
        await store.access_token()  # never signed in
    http.add(TOKEN_URL, 200, {"access_token": "A1", "refresh_token": "R1", "expires_in": 0})
    await store.exchange("C", "V")
    http.answers[TOKEN_URL] = [HttpResult(500, {}, None)]
    store.forget()
    with pytest.raises(SpotifyAuthError, match="HTTP 500"):
        await store.access_token()  # a server error: not the end of the sign-in
    http.answers[TOKEN_URL] = [HttpResult(400, {}, {"error": "invalid_grant"})]
    with pytest.raises(SpotifySignInExpired):
        await store.access_token()
    asked = len(http.requests)
    with pytest.raises(SpotifySignInExpired):
        await store.access_token()  # ended: no more refresh attempts until a new sign-in
    assert len(http.requests) == asked


async def test_the_paste_login(tmp_path: Path) -> None:
    http = FakeHttp()
    http.add(TOKEN_URL, 200, {"access_token": "A1", "refresh_token": "R1", "expires_in": 3600})
    store = tokens(tmp_path, http)
    shown: list[str] = []

    def read(prompt: str) -> str:
        url = next(line for line in shown if line.startswith("https://accounts.spotify.com/authorize"))
        state = parse_qs(urlsplit(url).query)["state"][0]
        return f"http://127.0.0.1:8888/callback?code=THECODE&state={state}"

    await login(store, read=read, write=shown.append)
    assert http.requests[0][2]["code"] == "THECODE"
    assert store.signed_in() and any("signed in" in line for line in shown)


async def test_an_unreadable_token_file_is_not_signed_in_and_an_ended_sign_in_shows(tmp_path: Path) -> None:
    http = FakeHttp()
    store = tokens(tmp_path, http)
    store.path.parent.mkdir(parents=True)
    store.path.write_bytes(b"\xff\xfe not json")
    assert not store.signed_in() and store.expires_at() is None
    http.add(TOKEN_URL, 200, {"access_token": "A1", "refresh_token": "R1", "expires_in": 0})
    await store.exchange("C", "V")
    http.answers[TOKEN_URL] = [HttpResult(400, {}, {"error": "invalid_grant"})]
    with pytest.raises(SpotifySignInExpired):
        await store.access_token()
    assert not store.signed_in()  # mini_status no longer says "signed in until …"


# ------------------------------------------------------------------ the paste's question off a terminal (setup's window)


def test_off_a_terminal_the_paste_is_asked_on_a_marked_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setup's window runs the sign-in with a pipe for stdin: the question goes out on a line of its own after the
    window's mark (as estia-deploy --ask-lines asks), and one line is read back."""
    import io

    from local_assistant import spotify_auth

    out = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO("http://127.0.0.1:8888/callback?code=c&state=s\n"))
    monkeypatch.setattr("sys.stdout", out)
    assert spotify_auth.read_answer("3. Paste the address here: ") == "http://127.0.0.1:8888/callback?code=c&state=s\n"
    assert out.getvalue() == "\x1f? 3. Paste the address here:\n"


def test_at_a_terminal_the_paste_is_asked_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    from local_assistant import spotify_auth

    asked: list[str] = []
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: noted(asked, prompt, "typed"))
    assert spotify_auth.read_answer("3. Paste the address here: ") == "typed"
    assert asked == ["3. Paste the address here: "]


def test_the_sign_in_reads_its_paste_with_that_reader() -> None:
    from local_assistant import deploy, entry, setup_admin, spotify_auth

    assert "read=spotify_auth.read_answer" in Path(entry.__file__).read_text(encoding="utf-8")
    assert spotify_auth.ASK_MARK == deploy.ASK_MARK == setup_admin.ASK_MARK


@pytest.mark.parametrize("pasted", ["", "  \n"])
def test_nothing_pasted_says_so(pasted: str) -> None:
    """Enter with nothing (or the window's Later, which ends its input): said plainly, never "state differs"."""
    from local_assistant.spotify_auth import code_from

    with pytest.raises(SpotifyAuthError, match=r"^nothing pasted; nothing saved$"):
        code_from(pasted, "s")
