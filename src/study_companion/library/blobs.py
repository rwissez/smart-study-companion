"""内容寻址的原始文件存储。

* 同一字节只存一次，按 SHA-256 定位，不按文件名；
* 写入先落到同卷临时文件再原子改名，避免半文件被读到；
* 读取只接受库内登记的 ``storage_key``，拒绝任意路径与越界；
* 原字节永不改写（06 §4.2）。
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from study_companion.config import Settings, get_settings
from study_companion.contracts import DomainError


@dataclass(frozen=True)
class StoredBlob:
    sha256: str
    byte_size: int
    storage_key: str

    @property
    def relative_key(self) -> str:
        return self.storage_key


def _root(settings: Settings | None = None) -> Path:
    return (settings or get_settings()).blobs_root


def _key_for(digest: str) -> str:
    return f"{digest[:2]}/{digest}"


def store_bytes(data: bytes, settings: Settings | None = None) -> StoredBlob:
    digest = hashlib.sha256(data).hexdigest()
    root = _root(settings)
    key = _key_for(digest)
    target = root / key
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.stat().st_size != len(data):
        handle, temp_name = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, target)
        except Exception:
            Path(temp_name).unlink(missing_ok=True)
            raise
    return StoredBlob(sha256=digest, byte_size=len(data), storage_key=key)


def resolve(storage_key: str, settings: Settings | None = None) -> Path:
    root = _root(settings).resolve()
    candidate = (root / storage_key).resolve()
    if not str(candidate).startswith(str(root) + os.sep):
        raise DomainError("FORBIDDEN", "非法的文件定位")
    if not candidate.is_file():
        raise DomainError("NOT_FOUND", "原始文件不存在")
    return candidate


def read_bytes(storage_key: str, settings: Settings | None = None) -> bytes:
    return resolve(storage_key, settings).read_bytes()


def verify(storage_key: str, sha256: str, settings: Settings | None = None) -> Path:
    path = resolve(storage_key, settings)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != sha256:
        raise DomainError("EXPORT_ARTIFACT_INVALID", "原始文件哈希校验失败")
    return path
