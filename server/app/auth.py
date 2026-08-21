"""Bearer token check.

The only thing standing in front of the API. On loopback that hardly matters;
the moment BIND_HOST reaches past this machine, this is the perimeter.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Query, status

from .config import Settings


def make_auth_dependency(settings: Settings):
    def verify(
        authorization: str | None = Header(default=None),
        # EventSource can't send headers. The PWA streams over fetch precisely
        # so it can use the header, but the query form keeps `curl` usable.
        token: str | None = Query(default=None),
    ) -> None:
        presented = token
        if authorization and authorization.lower().startswith("bearer "):
            presented = authorization[7:].strip()

        if not presented or not hmac.compare_digest(presented, settings.auth_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or missing token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return verify
