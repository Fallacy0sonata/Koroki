from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


class AuthStore:
    def __init__(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        self._data_dir = repo_root / "data" / "web_auth"
        self._users_path = self._data_dir / "users.json"
        self._sessions_path = self._data_dir / "sessions.json"

    def register_user(self, username: str, password: str, email: str | None = None) -> dict[str, Any]:
        username_key = self._normalize_username(username)
        email_key = self._normalize_email(email)
        users = self._load_json(self._users_path, default={"users": []})
        if any(user["username_key"] == username_key for user in users["users"]):
            raise ValueError("That username is already claimed.")
        if email_key and any(self._user_email_key(user) == email_key for user in users["users"]):
            raise ValueError("This email already has an account.")

        now = self._iso_now()
        user_id = f"web_{secrets.token_hex(8)}"
        salt = secrets.token_hex(16)
        password_hash = self._hash_password(password, salt)
        user_record = {
            "user_id": user_id,
            "username": username.strip(),
            "username_key": username_key,
            "email": (email or "").strip(),
            "email_key": email_key,
            "password_hash": password_hash,
            "salt": salt,
            "created_at": now,
            "updated_at": now,
        }
        users["users"].append(user_record)
        self._write_json(self._users_path, users)
        return self._public_user(user_record)

    def authenticate_user(self, username: str, password: str) -> tuple[dict[str, Any] | None, str | None]:
        username_key = self._normalize_username(username)
        users = self._load_json(self._users_path, default={"users": []})
        target_user: dict[str, Any] | None = None
        for user in users["users"]:
            if user["username_key"] != username_key:
                continue
            target_user = user
            expected = self._hash_password(password, user["salt"])
            if hmac.compare_digest(expected, user["password_hash"]):
                return self._public_user(user), None
            return None, "The password is wrong."
        if target_user is None:
            return None, "This username doesn't exist."
        return None, "Login failed."

    def create_session(self, user_id: str) -> str:
        sessions = self._load_json(self._sessions_path, default={"sessions": {}})
        token = secrets.token_urlsafe(32)
        sessions["sessions"][token] = {
            "user_id": user_id,
            "created_at": self._iso_now(),
            "expires_at": self._iso_now(offset_days=30),
        }
        self._write_json(self._sessions_path, sessions)
        return token

    def get_session_user(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None

        sessions = self._load_json(self._sessions_path, default={"sessions": {}})
        session = sessions["sessions"].get(token)
        if not session:
            return None

        expires_at = self._parse_iso(session["expires_at"])
        if expires_at <= datetime.now(UTC):
            sessions["sessions"].pop(token, None)
            self._write_json(self._sessions_path, sessions)
            return None

        users = self._load_json(self._users_path, default={"users": []})
        for user in users["users"]:
            if user["user_id"] == session["user_id"]:
                return self._public_user(user)
        return None

    def delete_session(self, token: str | None) -> None:
        if not token:
            return
        sessions = self._load_json(self._sessions_path, default={"sessions": {}})
        if token in sessions["sessions"]:
            sessions["sessions"].pop(token, None)
            self._write_json(self._sessions_path, sessions)

    @staticmethod
    def _normalize_username(username: str) -> str:
        normalized = "".join(ch for ch in username.strip().lower() if ch.isalnum() or ch in ("_", "-", "."))
        if len(normalized) < 3:
            raise ValueError("Username must have at least 3 valid characters.")
        return normalized[:32]

    @staticmethod
    def _normalize_email(email: str | None) -> str:
        normalized = (email or "").strip().lower()
        return normalized[:120]

    def _user_email_key(self, user: dict[str, Any]) -> str:
        return self._normalize_email(user.get("email_key") or user.get("email"))

    @staticmethod
    def _hash_password(password: str, salt: str) -> str:
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            120_000,
        )
        return digest.hex()

    @staticmethod
    def _public_user(user: dict[str, Any]) -> dict[str, Any]:
        return {
            "user_id": user["user_id"],
            "username": user["username"],
            "email": user.get("email", ""),
            "created_at": user["created_at"],
        }

    @staticmethod
    def _iso_now(offset_days: int = 0) -> str:
        return (datetime.now(UTC) + timedelta(days=offset_days)).isoformat()

    @staticmethod
    def _parse_iso(value: str) -> datetime:
        return datetime.fromisoformat(value)

    def _load_json(self, path: Path, default: dict[str, Any]) -> dict[str, Any]:
        if not path.exists():
            return default
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2)


auth_store = AuthStore()
