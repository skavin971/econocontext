import hashlib
import json
import os
import tempfile
from pathlib import Path

from ..contracts import canonical


class Artifacts:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes) -> str:
        key = hashlib.sha256(data).hexdigest()
        target = self.root / key
        if not target.exists():
            fd, name = tempfile.mkstemp(dir=self.root)
            try:
                with os.fdopen(fd, "wb") as out:
                    out.write(data)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(name, target)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        return key

    def get(self, key: str) -> bytes:
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("Invalid artifact hash")
        data = (self.root / key).read_bytes()
        if hashlib.sha256(data).hexdigest() != key:
            raise ValueError("Artifact integrity failure")
        return data

    def json(self, value) -> str:
        return self.put(canonical(value).encode())

    def read_json(self, key):
        return json.loads(self.get(key))
