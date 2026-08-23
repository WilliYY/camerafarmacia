import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .storage import UnsafeAnalyticsPathError, _is_within, _iso


MAX_PROFILES = 100
MAX_PROTECTED_PROFILE_BYTES = 128 * 1024
MAX_DATABASE_BYTES = 16 * 1024 * 1024


class BiometricStore:
    def __init__(self, path, forbidden_roots=None):
        self.path = Path(path).resolve()
        for root in forbidden_roots or []:
            resolved_root = Path(root).resolve()
            if _is_within(self.path, resolved_root):
                raise UnsafeAnalyticsPathError("biometric_database_inside_recording_root")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.last_cleanup_error = None
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA secure_delete=ON")
        connection.execute("PRAGMA journal_size_limit=2097152")
        connection.execute("PRAGMA wal_autocheckpoint=100")
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        connection.execute(f"PRAGMA max_page_count={MAX_DATABASE_BYTES // page_size}")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self):
        with self._lock, self._connection() as connection:
            previous_version = connection.execute("PRAGMA user_version").fetchone()[0]
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS biometric_profiles (
                    profile_id TEXT PRIMARY KEY,
                    protected_profile BLOB NOT NULL,
                    consent_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS biometric_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    action TEXT NOT NULL,
                    profile_id TEXT NOT NULL
                );
                """
            )
        if previous_version < 1:
            try:
                self._compact_sensitive_store()
            except Exception as error:
                self.last_cleanup_error = str(error)[:160]
            else:
                with self._lock, self._connection() as connection:
                    connection.execute("PRAGMA user_version=1")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def create_profile(self, protected_profile, consent_at=None):
        if not isinstance(protected_profile, (bytes, bytearray)):
            raise ValueError("invalid_protected_profile")
        protected_profile = bytes(protected_profile)
        if not protected_profile or len(protected_profile) > MAX_PROTECTED_PROFILE_BYTES:
            raise ValueError("invalid_protected_profile")
        profile_id = str(uuid.uuid4())
        now = datetime.now()
        consent_at = _iso(consent_at or now)
        with self._lock, self._connection() as connection:
            count = connection.execute("SELECT COUNT(*) FROM biometric_profiles").fetchone()[0]
            if count >= MAX_PROFILES:
                raise ValueError("biometric_profile_limit_reached")
            connection.execute(
                """
                INSERT INTO biometric_profiles(
                    profile_id, protected_profile, consent_at, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (profile_id, protected_profile, consent_at, _iso(now)),
            )
            connection.execute(
                "INSERT INTO biometric_audit(occurred_at, action, profile_id) VALUES (?, ?, ?)",
                (_iso(now), "created", profile_id),
            )
        return profile_id

    def list_profiles(self, include_payload=False):
        columns = "profile_id, protected_profile, consent_at, created_at"
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                f"SELECT {columns} FROM biometric_profiles ORDER BY created_at, profile_id"
            ).fetchall()
        results = []
        for row in rows:
            item = {
                "profile_id": row["profile_id"],
                "consent_at": row["consent_at"],
                "created_at": row["created_at"],
            }
            if include_payload:
                item["protected_profile"] = bytes(row["protected_profile"])
            results.append(item)
        return results

    def update_profile(self, profile_id, protected_profile):
        if not isinstance(protected_profile, (bytes, bytearray)):
            raise ValueError("invalid_protected_profile")
        protected_profile = bytes(protected_profile)
        if not protected_profile or len(protected_profile) > MAX_PROTECTED_PROFILE_BYTES:
            raise ValueError("invalid_protected_profile")
        profile_id = str(profile_id)
        now = datetime.now()
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "UPDATE biometric_profiles SET protected_profile = ? WHERE profile_id = ?",
                (protected_profile, profile_id),
            )
            if not cursor.rowcount:
                return False
            connection.execute(
                "INSERT INTO biometric_audit(occurred_at, action, profile_id) VALUES (?, ?, ?)",
                (_iso(now), "updated", profile_id),
            )
        return True

    def delete_profile(self, profile_id):
        profile_id = str(profile_id)
        now = datetime.now()
        deleted = False
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM biometric_profiles WHERE profile_id = ?",
                (profile_id,),
            )
            if cursor.rowcount:
                deleted = True
                connection.execute(
                    "INSERT INTO biometric_audit(occurred_at, action, profile_id) VALUES (?, ?, ?)",
                    (_iso(now), "deleted", profile_id),
                )
        if deleted:
            self.last_cleanup_error = None
            try:
                self._compact_sensitive_store()
            except Exception as error:
                self.last_cleanup_error = str(error)[:160]
                try:
                    with self._lock, self._connection() as connection:
                        connection.execute("PRAGMA user_version=0")
                except Exception:
                    pass
        return deleted

    def _compact_sensitive_store(self):
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
                connection.execute("VACUUM")
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            finally:
                connection.close()

    def close(self):
        return None
