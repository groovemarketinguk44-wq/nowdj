import os
import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import Header, HTTPException, Request

SECRET_KEY = os.environ.get("SECRET_KEY", "nowdj-dev-secret-change-me-in-production")
ALGORITHM  = "HS256"


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except Exception:
        return False


def create_token(role: str, uid: int, email: str, name: str, tenant_id: int | None = None) -> str:
    exp = datetime.now(timezone.utc) + timedelta(days=30)
    payload: dict = {"role": role, "uid": uid, "email": email, "name": name, "exp": exp}
    if tenant_id is not None:
        payload["tenant_id"] = tenant_id
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except Exception:
        return None


# ── FastAPI dependencies ────────────────────────────────────────────────────

def _require_role(role: str, authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(authorization[7:])
    if not payload or payload.get("role") != role:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return payload


async def require_customer(authorization: str = Header(default=None)) -> dict:
    return _require_role("customer", authorization)


async def require_staff(authorization: str = Header(default=None)) -> dict:
    return _require_role("staff", authorization)


async def require_tenant_admin(
    request: Request,
    authorization: str = Header(default=None),
) -> dict:
    payload = _require_role("tenant_admin", authorization)
    # Verify the token's tenant matches the subdomain the request arrived on
    tenant = getattr(request.state, "tenant", None)
    if tenant and payload.get("tenant_id") != tenant["id"]:
        raise HTTPException(status_code=403, detail="Token does not match this workspace")
    return payload


async def require_super_admin(authorization: str = Header(default=None)) -> dict:
    return _require_role("super_admin", authorization)
