import os
import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import Header, HTTPException

SECRET_KEY = os.environ.get("SECRET_KEY", "nowdj-dev-secret-change-me-in-production")
ALGORITHM  = "HS256"


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except Exception:
        return False


def create_token(role: str, uid: int, email: str, name: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(days=30)
    return jwt.encode(
        {"role": role, "uid": uid, "email": email, "name": name, "exp": exp},
        SECRET_KEY, algorithm=ALGORITHM,
    )


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
