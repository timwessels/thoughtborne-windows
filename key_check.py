"""
Cheap live API-key validation for the settings app's "Test key" button (#144).

Stdlib only (urllib.request) so it runs under any interpreter without the uv
venv -- the settings app must work on a half-set-up first run where the `groq` /
`httpx` packages are not importable. Each check is a tiny authenticated GET
(`Authorization: Bearer <key>`) with a short timeout: no transcription, no
billable work. The KEY is NEVER logged or echoed (details are generic).

Two things here are not obvious and must not be tidied away (#205): every request
carries an explicit `User-Agent` (see USER_AGENT), and a status that is neither 2xx
nor 401 is its own verdict (INCONCLUSIVE), never UNREACHABLE -- a server that
answers has not failed to answer, whatever it says.

`classify_http` is a pure function (unit-tested); the live round-trip is a
hands-on test. Run the checks off the UI thread -- the timeout bounds them.
"""

import http.client
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum


class KeyStatus(Enum):
    VALID = "valid"                # 2xx -> the key authenticates (green)
    INVALID = "invalid"            # 401 -> the key is wrong / revoked (red)
    INCONCLUSIVE = "inconclusive"  # answered, but not about the key (amber)
    UNREACHABLE = "unreachable"    # no answer arrived at all (grey)


@dataclass
class KeyResult:
    status: KeyStatus
    detail: str


def classify_http(status_code: int) -> KeyStatus:
    """Pure: map an HTTP status to a KeyStatus. 2xx -> VALID (the key authenticates),
    401 -> INVALID (the key is wrong or revoked), anything else -> INCONCLUSIVE: the
    server answered, but not about the key -- a WAF 403, a 404 on a moved endpoint, a
    rate limit, a 5xx. None of those is a network fault and none is a verdict, so
    reporting either would be a lie (#205).

    UNREACHABLE is deliberately NOT reachable from here: it now means no answer
    arrived at all, which only the caller's transport-exception path can know."""
    if 200 <= status_code < 300:
        return KeyStatus.VALID
    if status_code == 401:
        return KeyStatus.INVALID
    return KeyStatus.INCONCLUSIVE


# Every check sends this explicitly. urllib's default UA ("Python-urllib/3.x") is
# banned outright by Groq's WAF: /models then answers a fast, key-INDEPENDENT 403
# (Cloudflare "error code: 1010") for a good key, a bad key and no key alike, which
# the old classification read as UNREACHABLE -- so a perfectly working key sent the
# user hunting network problems that did not exist (#205). Measured 2026-08-25: with
# any non-urllib UA the same endpoint answers honestly (200 / 401), and Soniox is
# unaffected by the header either way. Identifying the client is also plain good HTTP
# manners. Keep it plain: a decoration is an unmeasured variant against a rule set
# that grades exactly such signatures.
USER_AGENT = "Thoughtborne-KeyCheck/1.0"

# Groq: a well-known, cheap, auth-only GET (the OpenAI-compatible model list).
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
# Soniox: an authenticated GET against the documented transcriptions collection
# (Bearer scheme, the same account key Soniox Live uses). GET lists (creates
# nothing); a 2xx confirms the key. Endpoint web-confirmed 2026-07-21 -- a bad key
# returns a live 401 here, so it is a documented, verified auth check.
SONIOX_LIST_URL = "https://api.soniox.com/v1/transcriptions"


def _check_bearer(url: str, key: str, timeout: float) -> KeyResult:
    """Do the authenticated GET and classify. Only a connection/timeout/OS error --
    no answer at all -- is UNREACHABLE; an answered non-auth status is classified as
    INCONCLUSIVE by classify_http. The key is stripped (mirroring the .env writer) and
    a key that cannot form a valid latin-1 HTTP header value -- a control character (an
    embedded \\n/\\r) or a glyph not encodable as latin-1 (a smart quote off a web page)
    -> INVALID, up front: otherwise urllib raises while composing the Authorization
    header (a non-OSError) and kills the worker thread. The key never appears in a
    detail string."""
    if not key or not key.strip():
        return KeyResult(KeyStatus.INVALID, "no key provided")
    # Mirror the .env writer's .strip(): a padded key is stored stripped-and-working,
    # so it must TEST stripped too (else a padded Authorization header 401s as a
    # spurious INVALID). Then reject a key that is not a safe latin-1 header value -- a
    # control char (an embedded \n/\r splits the header) or a non-latin-1 glyph (a
    # smart quote) would raise inside urllib and escape as a non-OSError. Never echo
    # the key.
    key = key.strip()
    try:
        key.encode("latin-1")
        header_safe = not any(ord(c) < 0x20 or ord(c) == 0x7f for c in key)
    except UnicodeEncodeError:
        header_safe = False
    if not header_safe:
        return KeyResult(KeyStatus.INVALID, "malformed key")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}",
                                               "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            return KeyResult(classify_http(status), f"HTTP {status}")
    except urllib.error.HTTPError as e:
        return KeyResult(classify_http(e.code), f"HTTP {e.code}")
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError,
            ValueError, UnicodeError):
        # http.client.HTTPException (e.g. BadStatusLine from a captive portal or
        # proxy sending a non-HTTP response) is NOT an OSError and would otherwise
        # escape urllib raw and kill the worker thread (B2). ValueError/UnicodeError
        # are a belt-and-braces net: any residual header-encoding surprise the guard
        # above missed still degrades to UNREACHABLE instead of crashing. Every branch
        # here is a request that got NO answer -- the only thing UNREACHABLE means (#205).
        return KeyResult(KeyStatus.UNREACHABLE, "could not reach the server")


def check_groq_key(key: str, *, timeout: float = 5.0) -> KeyResult:
    """Validate a Groq API key with an auth-only GET on the model list
    (GET /openai/v1/models, web-confirmed as an auth-only endpoint). No
    transcription. 401 -> INVALID; 2xx -> VALID; any other answered status ->
    INCONCLUSIVE; no answer at all -> UNREACHABLE."""
    return _check_bearer(GROQ_MODELS_URL, key, timeout)


def check_soniox_key(key: str, *, timeout: float = 5.0) -> KeyResult:
    """Validate a Soniox API key with a cheap authenticated GET (no work created).
    401 -> INVALID; 2xx -> VALID; any other answered status -> INCONCLUSIVE; no answer
    at all -> UNREACHABLE. Hits the web-confirmed transcriptions endpoint (see
    SONIOX_LIST_URL)."""
    return _check_bearer(SONIOX_LIST_URL, key, timeout)
