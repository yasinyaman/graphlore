"""OAuth for the HTTP transport: graphlore as a resource server.

Remote MCP clients (claude.ai and ChatGPT connectors, Claude Code over HTTP)
sign in with OAuth 2.1 as the MCP specification describes: the server
publishes RFC 9728 Protected Resource Metadata naming its authorization
server, answers a request without a valid token with 401 and a
``WWW-Authenticate: Bearer resource_metadata=…`` header, and accepts only
tokens issued for it. graphlore issues no token: any OAuth / OpenID provider
does (Auth0, Keycloak, Entra ID, Okta, Google…), and the clients register with
it themselves.

``GRAPHLORE_AUTH_ISSUER`` (the provider's issuer URL) and
``GRAPHLORE_AUTH_RESOURCE_URL`` (this server's public URL, the token audience)
turn it on; ``GRAPHLORE_AUTH_SCOPES`` (comma-separated) are required of every
token (403 ``insufficient_scope`` otherwise). `JwtVerifier` checks a JWT's
signature against the provider's keys (found by RFC 8414 / OpenID discovery),
its issuer, audience and expiry; the SDK's middleware does the rest (401/403,
the metadata route). The discovery and key fetches reach the network, so
``GRAPHLORE_OFFLINE`` refuses this mode.

Needs the ``remote`` extra (pyjwt[crypto]), imported only when configured.
Imports only ``config``.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from . import config

# Signature algorithms a provider's JWT may use: asymmetric only (a shared
# secret, or "none", would let a token be made without the provider's key).
ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512",
              "ES256", "ES384", "ES512", "EdDSA"]
_TIMEOUT = 10


class AuthConfigError(Exception):
    """OAuth is asked for but cannot run (offline, the extra missing, a URL wrong)."""


def settings() -> dict[str, Any] | None:
    """``{issuer, resource, scopes}`` from the environment, or None when OAuth
    is not asked for; an error when it is asked for half."""
    issuer = (config.env("AUTH_ISSUER") or "").strip().rstrip("/")
    resource = (config.env("AUTH_RESOURCE_URL") or "").strip()
    if not issuer and not resource:
        return None
    if not issuer or not resource:
        raise AuthConfigError(
            "OAuth needs both GRAPHLORE_AUTH_ISSUER (the provider's issuer URL) and "
            "GRAPHLORE_AUTH_RESOURCE_URL (this server's public URL).")
    for name, url in (("GRAPHLORE_AUTH_ISSUER", issuer),
                      ("GRAPHLORE_AUTH_RESOURCE_URL", resource)):
        parts = urlsplit(url)
        if parts.scheme != "https" and parts.hostname not in ("localhost", "127.0.0.1"):
            raise AuthConfigError(f"{name} must be an https URL (or localhost): {url}")
    scopes = [s.strip() for s in (config.env("AUTH_SCOPES") or "").split(",") if s.strip()]
    return {"issuer": issuer, "resource": resource, "scopes": scopes}


def discovery_urls(issuer: str) -> list[str]:
    """Where an issuer's metadata is: RFC 8414 (the well-known path inserted
    before the issuer's own path), then OpenID Connect discovery."""
    parts = urlsplit(issuer)
    path = parts.path.rstrip("/")
    rfc8414 = urlunsplit((parts.scheme, parts.netloc,
                          "/.well-known/oauth-authorization-server" + path, "", ""))
    oidc = issuer.rstrip("/") + "/.well-known/openid-configuration"
    return [rfc8414, oidc]


def _get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=_TIMEOUT) as resp:  # noqa: S310 - https URL checked
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{url} did not return a JSON object")
    return data


def jwks_uri(issuer: str) -> str:
    """The provider's key set URL, from its metadata (whose issuer must match)."""
    errors = []
    for url in discovery_urls(issuer):
        try:
            meta = _get_json(url)
        except Exception as e:  # noqa: BLE001 - try the next form, report all
            errors.append(f"{url}: {e}")
            continue
        if str(meta.get("issuer", "")).rstrip("/") != issuer:
            errors.append(f"{url}: its issuer is {meta.get('issuer')!r}, not {issuer!r}")
            continue
        if meta.get("jwks_uri"):
            return str(meta["jwks_uri"])
        errors.append(f"{url}: no jwks_uri")
    raise AuthConfigError("could not find the provider's keys: " + "; ".join(errors))


class JwtVerifier:
    """The SDK's ``TokenVerifier``: a JWT signed by the provider, for this
    resource (``aud``), from this issuer (``iss``), not expired. Scopes are
    read from ``scope`` (space-separated) or ``scp`` (a list) and checked by
    the SDK's middleware against the required ones."""

    def __init__(self, issuer: str, resource: str, keys: Any = None) -> None:
        try:
            import jwt  # noqa: F401
        except ImportError as e:
            raise AuthConfigError(
                "OAuth needs the remote extra: pip install 'graphlore[remote]'") from e
        self.issuer = issuer
        self.resource = resource
        # a PyJWKClient (or anything with get_signing_key_from_jwt), made on first use
        self._keys = keys

    def _signing_key(self, token: str) -> Any:
        import jwt

        if self._keys is None:
            if config.offline():
                raise AuthConfigError("GRAPHLORE_OFFLINE: the provider's keys cannot be fetched")
            self._keys = jwt.PyJWKClient(jwks_uri(self.issuer), cache_keys=True,
                                         lifespan=3600, timeout=_TIMEOUT)
        return self._keys.get_signing_key_from_jwt(token).key

    def verify(self, token: str) -> Any:
        """The token's ``AccessToken``, or None when it is not one for us."""
        import jwt
        from mcp.server.auth.provider import AccessToken

        try:
            claims = jwt.decode(
                token, self._signing_key(token), algorithms=ALGORITHMS,
                audience=self.resource, issuer=self.issuer,
                options={"require": ["exp", "iss", "aud"]})
        except (jwt.PyJWTError, AuthConfigError, ValueError, KeyError):
            return None
        raw = claims.get("scope")
        scopes = raw.split() if isinstance(raw, str) else list(claims.get("scp") or [])
        client = claims.get("client_id") or claims.get("azp") or claims.get("sub") or ""
        return AccessToken(
            token=token, client_id=str(client), scopes=[str(s) for s in scopes],
            expires_at=int(claims["exp"]), resource=self.resource,
            subject=str(claims["sub"]) if claims.get("sub") else None,
            claims={"iss": claims["iss"]})

    async def verify_token(self, token: str) -> Any:
        import anyio

        # the key fetch blocks: off the event loop
        return await anyio.to_thread.run_sync(self.verify, token)


def apply(mcp: Any, conf: dict[str, Any], keys: Any = None) -> None:
    """Make ``mcp``'s HTTP app require these tokens: the SDK reads its auth
    settings and token verifier when it builds the app."""
    from mcp.server.auth.settings import AuthSettings

    if config.offline() and keys is None:
        raise AuthConfigError("GRAPHLORE_OFFLINE is set: OAuth needs the provider's keys "
                              "from the network")
    mcp.settings.auth = AuthSettings(
        issuer_url=conf["issuer"], resource_server_url=conf["resource"],
        required_scopes=conf["scopes"] or None,
        # the verifier checks the audience itself
        validate_token_resource=False)
    mcp._token_verifier = JwtVerifier(conf["issuer"], conf["resource"], keys)
