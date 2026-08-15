from __future__ import annotations

import asyncio
import time
from typing import Any

import jwt
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken

from .config import Settings


class JwtTokenVerifier:
    """Verify access tokens issued by an external OAuth 2.1 provider."""

    def __init__(self, settings: Settings):
        if not settings.oauth_jwks_url or not settings.oauth_issuer or not settings.oauth_audience:
            raise ValueError("OAuth verifier is missing issuer, audience, or JWKS URL")
        # JWT issuer comparison is exact. Auth0 issuers conventionally end in
        # a slash, so preserve the discovery-document value verbatim.
        self.issuer = settings.oauth_issuer.strip()
        self.audience = settings.oauth_audience
        self.required_scopes = set(settings.oauth_scopes)
        self.jwks_client = jwt.PyJWKClient(settings.oauth_jwks_url, cache_keys=True, lifespan=300)

    def _verify_sync(self, token: str) -> AccessToken | None:
        try:
            signing_key = self.jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "ES256", "EdDSA"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "sub"]},
            )
            scope_value: Any = claims.get("scope", claims.get("scp", []))
            if isinstance(scope_value, str):
                scopes = [item for item in scope_value.split() if item]
            elif isinstance(scope_value, list):
                scopes = [str(item) for item in scope_value]
            else:
                scopes = []
            if self.required_scopes and not self.required_scopes.issubset(scopes):
                return None
            return AccessToken(
                token=token,
                client_id=str(claims.get("azp") or claims.get("client_id") or "chatgpt"),
                scopes=scopes,
                expires_at=int(claims.get("exp", time.time())),
                resource=self.audience,
                subject=str(claims["sub"]),
                claims={key: value for key, value in claims.items() if key not in {"nonce"}},
            )
        except (jwt.PyJWTError, ValueError, TypeError):
            return None

    async def verify_token(self, token: str) -> AccessToken | None:
        return await asyncio.to_thread(self._verify_sync, token)


def request_namespace(settings: Settings) -> str:
    if settings.auth_mode == "none":
        return "local-single-user"
    access_token = get_access_token()
    if access_token is None or not access_token.subject:
        raise PermissionError("authenticated user subject is unavailable")
    issuer = str((access_token.claims or {}).get("iss") or settings.oauth_issuer or "oauth")
    return f"{issuer}|{access_token.subject}"


def security_schemes(settings: Settings) -> list[dict[str, Any]]:
    if settings.auth_mode == "oauth":
        return [{"type": "oauth2", "scopes": list(settings.oauth_scopes)}]
    return [{"type": "noauth"}]
