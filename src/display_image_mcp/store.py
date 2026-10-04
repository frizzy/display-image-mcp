"""On-disk image store: one image per (profile, label), overwritten on update."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from .profiles import NAME_RE

CONTENT_TYPES = {"bmp": "image/bmp", "png": "image/png"}


def check_label(label: str) -> str:
    if not NAME_RE.match(label):
        raise ValueError(f"invalid label {label!r} (lowercase letters, digits, - and _; max 64 chars)")
    return label


class ImageStore:
    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, profile: str) -> Path:
        return self.root / check_label(profile)

    def _paths(self, profile: str, label: str, fmt: str) -> tuple[Path, Path]:
        d = self._dir(profile)
        return d / f"{check_label(label)}.{fmt}", d / f"{label}.json"

    def save(self, profile: str, label: str, data: bytes, fmt: str, width: int, height: int,
             description: str = "") -> dict:
        """Store an image atomically. Returns its metadata plus `changed` (bytes differ from the previous one)."""
        img_path, meta_path = self._paths(profile, label, fmt)
        digest = hashlib.sha256(data).hexdigest()
        with self._lock:
            previous = self._read_meta(meta_path)
            changed = previous is None or previous["sha256"] != digest
            meta = {
                "label": label,
                "profile": profile,
                "format": fmt,
                "content_type": CONTENT_TYPES[fmt],
                "width": width,
                "height": height,
                "size": len(data),
                "sha256": digest,
                "description": description,
                # updated_at only moves when the picture actually changed
                "updated_at": (datetime.now(timezone.utc).isoformat(timespec="seconds")
                               if changed else previous["updated_at"]),
            }
            img_path.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(img_path, data)
            self._atomic_write(meta_path, json.dumps(meta, indent=2).encode())
        return {**meta, "changed": changed}

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    @staticmethod
    def _read_meta(path: Path) -> dict | None:
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def get(self, profile: str, label: str) -> tuple[dict, bytes] | None:
        """Return (metadata, bytes) or None."""
        d = self._dir(profile)
        meta = self._read_meta(d / f"{check_label(label)}.json")
        if meta is None:
            return None
        try:
            return meta, (d / f"{label}.{meta['format']}").read_bytes()
        except FileNotFoundError:
            return None

    def list(self, profile: str) -> list[dict]:
        d = self._dir(profile)
        if not d.is_dir():
            return []
        metas = (self._read_meta(p) for p in sorted(d.glob("*.json")))
        return [m for m in metas if m]

    def delete(self, profile: str, label: str) -> bool:
        d = self._dir(profile)
        with self._lock:
            meta = self._read_meta(d / f"{check_label(label)}.json")
            if meta is None:
                return False
            (d / f"{label}.{meta['format']}").unlink(missing_ok=True)
            (d / f"{label}.json").unlink(missing_ok=True)
        return True
