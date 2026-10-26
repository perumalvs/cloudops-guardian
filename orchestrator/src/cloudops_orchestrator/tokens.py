"""Approval token generation, keyed hashing and constant-time verification."""
import hashlib
import hmac
import secrets


def generate_token():
    """Plaintext token: emailed once, never stored or logged."""
    return secrets.token_urlsafe(32)


def hash_token(token, plan_id, pepper):
    """HMAC-SHA256 bound to the plan id and a secret pepper."""
    if not pepper:
        raise ValueError("APPROVAL_TOKEN_PEPPER is not configured")
    return hmac.new(pepper.encode(), f"{plan_id}:{token}".encode(), hashlib.sha256).hexdigest()


def verify_token(token, plan_id, pepper, stored_hash):
    if not token or not stored_hash:
        return False
    return hmac.compare_digest(hash_token(token, plan_id, pepper), stored_hash)
