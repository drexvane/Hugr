"""The dashboard's password gate: off unless an operator turns it on.

Read this before relying on it. **This is a shared-secret gate, not access control.**
It stops a passer-by who finds the URL. It does not:

* identify anyone - there are no users, only a password, so nothing can be revoked
  for one person or attributed to one person;
* rate-limit - a per-session attempt counter is the most this can do without a
  server-side store, and a determined guesser opens a new session;
* make it safe to host the clean data. `order_items` carries customer names, street
  addresses and 3,340 client IPs. No view surfaces them and `DIMENSIONS` cannot name
  them, but that is a display choice enforced by tests, not an authorisation
  boundary. Hosting *this* data for anyone outside the team wants a real identity
  provider in front of it, and `docs/02-dashboard-design.md` records that.

What it is for: putting the demo on a host without leaving it open, which is the
smallest defensible step and the one that unblocks Phase 4's launch task.

Two ways to configure it, both from the environment, and neither is stored here:

    DTP_DASHBOARD_PASSWORD=...            the password itself
    DTP_DASHBOARD_PASSWORD_SHA256=...     its hex sha256, if the platform's secret
                                          store is somewhere you would rather not
                                          keep the plaintext

The digest form is checked first when both are set. Nothing is written to disk and no
attempt is logged: a log of failed passwords is a log of passwords.

No Streamlit here. The decision of whether a supplied string is correct is logic, and
logic in the renderer is logic no test can reach - the same split `views.py` makes.
"""

from __future__ import annotations

import hashlib
import hmac
import os

PASSWORD_ENV = "DTP_DASHBOARD_PASSWORD"
DIGEST_ENV = "DTP_DASHBOARD_PASSWORD_SHA256"

# Below this, the gate is theatre. Twelve is not a cryptographic threshold; it is the
# point at which a password stops being guessable by someone typing.
MIN_LENGTH = 12

# Per session, and honestly labelled: it slows a browser-driven guess loop and does
# nothing to a script that opens sessions.
MAX_ATTEMPTS = 5


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def required() -> bool:
    """True when an operator has configured a secret, which is the only switch."""
    return bool(_env(PASSWORD_ENV) or _env(DIGEST_ENV))


def weakness() -> str:
    """Why the configured secret should be rejected, or "" when it is fine.

    Returned rather than raised so the caller can refuse to serve *and* say why. A
    dashboard that quietly accepts `1234` is worse than one that will not start: the
    operator believes it is protected.
    """
    digest = _env(DIGEST_ENV)
    if digest:
        if len(digest) != 64 or any(c not in "0123456789abcdefABCDEF"
                                    for c in digest):
            return (DIGEST_ENV + " is not a sha256 hex digest (64 hex characters). "
                    "Generate it with: python -c \"import hashlib,getpass;"
                    "print(hashlib.sha256(getpass.getpass().encode()).hexdigest())\"")
        return ""
    password = _env(PASSWORD_ENV)
    if len(password) < MIN_LENGTH:
        return (PASSWORD_ENV + " is shorter than " + str(MIN_LENGTH) + " characters. "
                "Use a longer one, or unset it to run without a gate.")
    return ""


def verify(supplied: str) -> bool:
    """Constant-time comparison against whichever form is configured.

    `hmac.compare_digest` rather than `==`, because `==` on strings returns as soon as
    two characters differ, and the time it takes is a description of the secret.
    """
    if not required() or weakness():
        return False
    digest = _env(DIGEST_ENV)
    if digest:
        offered = hashlib.sha256((supplied or "").encode("utf-8")).hexdigest()
        return hmac.compare_digest(offered, digest.lower())
    return hmac.compare_digest(supplied or "", _env(PASSWORD_ENV))
