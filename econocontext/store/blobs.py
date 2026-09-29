"""Content-addressed blob storage: large content is stored once, by the SHA-256 of its bytes.

Why it exists: the same large content (a file read by the root and again by a worker, a
repeated delegated result) otherwise lands in several rows. A blob is written once and
referenced by its key from any number of rows (`segments.blob_key`,
`stored_results.blob_key`).

  put(data) -> blob_key     blob_key = sha256(data) hex; storing identical bytes again is a no-op
  get(blob_key) -> bytes    KeyError if unknown

What it must never do: change or delete a blob. Keys are content: same bytes, same key.
"""

import hashlib
from datetime import datetime, timezone
from typing import Protocol


class BlobStore(Protocol):
    def put(self, data: bytes) -> str: ...

    def get(self, blob_key: str) -> bytes: ...


def blob_key(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class DBBlobStore:
    """Blobs in the Agent DB's own `blobs` table (bytea in Postgres)."""

    def __init__(self, db):
        self.db = db

    def put(self, data: bytes) -> str:
        key = blob_key(data)
        self.db.execute("INSERT OR IGNORE INTO blobs (blob_key, size, data, created_at) "
                        "VALUES(?,?,?,?)", (key, len(data), data, _now()))
        return key

    def get(self, key: str) -> bytes:
        rows = self.db.rows("SELECT data FROM blobs WHERE blob_key=?", (key,))
        if not rows:
            raise KeyError(key)
        return bytes(rows[0]["data"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
