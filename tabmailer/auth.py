"""Shared-password gate whose session survives a page refresh.

Streamlit session state is per-connection: refreshing the tab, opening a second
tab, or the app waking from sleep on Streamlit Cloud all start a brand new
session, which would sign everyone out constantly. So a successful login mints a
signed token that a tiny custom component parks in the browser's
``localStorage``, and every later run restores the session from it.

This is deliberately one shared credential rather than per-user identity -
everyone who knows it is the same "user" as far as the app is concerned. The
credential lives in ``st.secrets``/environment, never in the repo.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

import streamlit as st

STORAGE_KEY = "tabmailer_session"
DEFAULT_SESSION_DAYS = 30

_FLAG = "_auth_ok"
_USER = "_auth_user"
_KNOWN = "_auth_known_token"
_PENDING = "_auth_pending"
_SKIP_RESTORE = "_auth_skip_restore"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def _setting(name: str, default: str = "") -> str:
    """Read from [auth] in secrets, then the environment."""
    try:
        section = st.secrets.get("auth", {})
        value = section.get(name)
        if value not in (None, ""):
            return str(value)
    except Exception:  # noqa: BLE001 - no secrets.toml at all is fine
        pass
    return os.environ.get(f"TABMAILER_AUTH_{name.upper()}", default)


def is_configured() -> bool:
    return bool(_setting("username") and _setting("password"))


def _session_days() -> int:
    try:
        return max(1, int(_setting("session_days", str(DEFAULT_SESSION_DAYS))))
    except ValueError:
        return DEFAULT_SESSION_DAYS


def _signing_key() -> bytes:
    """Key used to sign session tokens.

    An explicit ``cookie_secret`` is preferred. Without one we derive a stable
    key from the credential itself, so that a redeploy does not silently log
    everybody out - which is the whole problem this module exists to solve.
    """
    explicit = _setting("cookie_secret")
    if explicit:
        return explicit.encode()
    seed = f"{_setting('username')}:{_setting('password')}:tabmailer".encode()
    return hashlib.sha256(seed).digest()


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def mint_token(username: str, days: int | None = None) -> str:
    payload = {
        "u": username,
        "exp": int(time.time()) + (days or _session_days()) * 86400,
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(_signing_key(), raw.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{raw}.{sig}"


def verify_token(token: str) -> str | None:
    """Return the username the token was issued to, or None if it's no good."""
    try:
        raw, _, sig = str(token).partition(".")
        if not raw or not sig:
            return None
        expected = hmac.new(_signing_key(), raw.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        if int(payload.get("exp", 0)) < time.time():
            return None
        return payload.get("u") or None
    except Exception:  # noqa: BLE001 - any malformed token is simply invalid
        return None


# ---------------------------------------------------------------------------
# Browser storage
#
# CCv2 components render inline in the page rather than in an iframe, so this
# reads and writes localStorage on the app's own origin.
# ---------------------------------------------------------------------------

_STORE = st.components.v2.component(
    "tabmailer_local_store",
    # Style is inline rather than in `css=`: a short single-line CSS string is
    # resolved as a file path and rejected for a non-packaged component.
    html='<span data-store style="display:none"></span>',
    js="""
export default function (component) {
  const { data, setStateValue } = component
  const KEY = data.storageKey
  let current = null
  try {
    if (data.op === "write" && typeof data.value === "string") {
      localStorage.setItem(KEY, data.value)
    } else if (data.op === "clear") {
      localStorage.removeItem(KEY)
    }
    current = localStorage.getItem(KEY)
  } catch (err) {
    current = null  // private window, or storage blocked by policy
  }
  // "" means "checked, nothing stored", which Python must be able to tell
  // apart from null = "this component has not reported back yet".
  const reported = current === null ? "" : current
  // Only emit on a real change, otherwise every rerun re-triggers a rerun.
  if (reported !== data.known) {
    setStateValue("token", reported)
  }
}
""",
)


def _sync_store() -> str | None:
    """Mount the store, applying any pending write/clear. Returns the token.

    None means the component has not reported yet; "" means it reported that
    nothing is stored.
    """
    op, value = st.session_state.pop(_PENDING, ("read", None))
    known = st.session_state.get(_KNOWN)
    try:
        result = _STORE(
            key="tabmailer_store",
            data={"storageKey": STORAGE_KEY, "op": op, "value": value, "known": known},
            default={"token": None},
            on_token_change=lambda: None,
        )
    except Exception:  # noqa: BLE001 - never let storage break the login
        return st.session_state.get(_KNOWN)

    token = getattr(result, "token", None)
    if token is not None:
        st.session_state[_KNOWN] = token
    return token if token is not None else known


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def _credentials_match(username: str, password: str) -> bool:
    expected_user = _setting("username")
    expected_pass = _setting("password")
    if not expected_user or not expected_pass:
        return False
    # compare_digest on both so a wrong username costs the same as a wrong
    # password.
    return hmac.compare_digest(username.strip(), expected_user) and hmac.compare_digest(
        password, expected_pass
    )


def _login_screen() -> None:
    st.markdown(
        """
        <style>
          [data-testid="stSidebar"], #MainMenu, header, footer {display:none !important;}
          .block-container {max-width: 420px; padding-top: 12vh;}
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("### Tableau Report Mailer")
    st.caption("Sign in to build and send reports.")

    with st.form("tabmailer_login"):
        username = st.text_input("Username", autocomplete="username")
        password = st.text_input("Password", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("Sign in", type="primary", width="stretch")

    if submitted:
        if _credentials_match(username, password):
            token = mint_token(username.strip())
            st.session_state[_FLAG] = True
            st.session_state[_USER] = username.strip()
            st.session_state[_KNOWN] = token
            st.session_state[_PENDING] = ("write", token)
            st.session_state.pop(_SKIP_RESTORE, None)
            st.rerun()
        else:
            st.error("Wrong username or password.")

    st.caption(f"You'll stay signed in on this browser for {_session_days()} days.")


def require_login() -> str:
    """Gate the whole app. Returns the signed-in username, or stops the script."""
    if not is_configured():
        st.error(
            "No login credentials are configured. Add an `[auth]` section with "
            "`username` and `password` to `.streamlit/secrets.toml` locally, or to "
            "**Settings -> Secrets** on Streamlit Cloud."
        )
        st.stop()

    # Mounted on every run, before the flag check, so that a pending write is
    # always applied and the element tree stays stable across reruns.
    token = _sync_store()

    if st.session_state.get(_FLAG):
        return st.session_state.get(_USER, "")

    if token:
        user = verify_token(token)
        if user:
            st.session_state[_FLAG] = True
            st.session_state[_USER] = user
            return user

    if token is None and not st.session_state.get(_SKIP_RESTORE):
        # localStorage has not answered yet. Waiting one round-trip avoids
        # flashing the login form at someone who is already signed in - but the
        # component might never report (storage blocked, JS error), so there is
        # always a way past this screen rather than a lockout.
        st.markdown(
            "<style>[data-testid='stSidebar'],#MainMenu,header,footer"
            "{display:none !important;}</style>"
            "<div style='padding-top:12vh;text-align:center;color:#6b7280'>"
            "Restoring your session…</div>",
            unsafe_allow_html=True,
        )
        _, mid, _ = st.columns([1, 1, 1])
        if mid.button("Sign in instead", width="stretch"):
            st.session_state[_SKIP_RESTORE] = True
            st.rerun()
        st.stop()

    _login_screen()
    st.stop()


def logout() -> None:
    st.session_state[_PENDING] = ("clear", None)
    st.session_state[_KNOWN] = ""
    for key in (_FLAG, _USER):
        st.session_state.pop(key, None)
