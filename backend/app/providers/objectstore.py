"""Where scanned pages live (doc 21 §1.3).

A patient's lab report is a few hundred kilobytes of JPEG per page, at roughly
500 patients a day. Postgres is the wrong home for that: it doubles the size of
every backup and every restore drill for bytes that never take part in a query.
So pages go to an object store and the database keeps the keys.

## Why this is not a `Provider`

Every other thing in this package wraps a *vendor* call and therefore meters and
prices it (`base.Provider._invoke`). Writing a file to the box's own disk is not
a vendor call: there is no per-unit price, and metering it would put rows into
`usage_events` that reconcile to nothing on the S18 dashboard. It keeps the
provider layer's other habits — one interface, config-selected, and a fake — but
not its billing machinery.

## Why the filesystem, and not S3/MinIO

The pilot's primary is the Omen box (`CODEBASE_MEMORY.md` → System Map), where a
Docker volume is the whole deployment story: no new service to run out of memory
at 2am, no credentials to rotate, no SigV4 to hand-roll against `httpx`. The
cloud shape gets an `S3ObjectStore` when the cloud shape becomes primary; the
interface below is the seam, and its absence is registered in
`STATE.md` → Stubs & fakes rather than pretended around.

What the filesystem store does **not** give us, and the operator must: the
backup job has to include this directory. Postgres alone is no longer a complete
restore — the extraction rows would come back pointing at pages that are gone.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

logger = logging.getLogger(__name__)

#: Keys are built by us, never by a client (`app.mrd.page_key`). The pattern is
#: enforced anyway, because "the caller always passes a safe key" is exactly the
#: assumption that turns one careless route into `../../etc/passwd`.
_KEY = re.compile(r"^[a-z0-9][a-z0-9/_.-]{0,200}$")


class ObjectStoreError(RuntimeError):
    """The store could not serve the call — disk full, key gone, bad key."""


class ObjectNotFound(ObjectStoreError):
    """No object at that key. A missing page is a visible state, not a 500."""


def validate_key(key: str) -> str:
    if not _KEY.match(key) or ".." in key or key.endswith("/"):
        raise ObjectStoreError(f"unsafe object key: {key!r}")
    return key


class ObjectStore(ABC):
    """Bytes in, bytes out, addressed by an opaque key."""

    kind: ClassVar[str] = "objectstore"
    name: ClassVar[str] = "abstract"

    @abstractmethod
    async def put(self, key: str, data: bytes, *, media_type: str = "image/jpeg") -> str:
        """Store `data` at `key`, overwriting. Returns the key."""

    @abstractmethod
    async def get(self, key: str) -> bytes:
        """Read one object. Raises `ObjectNotFound`."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove one object. Deleting a key that is already gone is not an error."""

    @abstractmethod
    async def exists(self, key: str) -> bool: ...


class FilesystemObjectStore(ObjectStore):
    """The real one: a directory on the box, one file per object.

    Writes are atomic (write a temp file in the same directory, then rename), so
    a crash mid-upload leaves no half-page for the extractor to read as a whole
    one. Files are `0600` under `0700` directories: the pages are clinical
    records, and the container's other processes have no business in them.
    """

    name: ClassVar[str] = "filesystem"

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self._root / validate_key(key)).resolve()
        root = self._root.resolve()
        # Belt and braces: `validate_key` already refuses `..`, and this refuses
        # anything that still escaped (a symlinked directory, say).
        if not path.is_relative_to(root):
            raise ObjectStoreError(f"key escapes the store root: {key!r}")
        return path

    def _put_sync(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".partial-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    async def put(self, key: str, data: bytes, *, media_type: str = "image/jpeg") -> str:
        # Off the event loop: a few hundred kilobytes to disk is short but not
        # free, and the api process is also serving the queue board.
        await asyncio.to_thread(self._put_sync, key, data)
        return key

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise ObjectNotFound(key) from exc
        except OSError as exc:
            raise ObjectStoreError(f"cannot read {key!r}: {exc}") from exc

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, True)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).is_file)

    def usage_bytes(self) -> int:
        """Total bytes held. For the admin storage figure — pages grow forever
        until someone is shown the number (doc 21 §8.7)."""
        return sum(f.stat().st_size for f in self._root.rglob("*") if f.is_file())

    def free_bytes(self) -> int:
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        return shutil.disk_usage(self._root).free


class FakeObjectStore(ObjectStore):
    """In-memory. Tests and the offline demo; never touches a disk."""

    name: ClassVar[str] = "fake"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.media_types: dict[str, str] = {}
        #: Set to raise on the next call — how tests drive the "disk is full"
        #: path without filling a disk.
        self.fail_with: Exception | None = None

    def _check(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    async def put(self, key: str, data: bytes, *, media_type: str = "image/jpeg") -> str:
        self._check()
        validate_key(key)
        self.objects[key] = data
        self.media_types[key] = media_type
        return key

    async def get(self, key: str) -> bytes:
        self._check()
        try:
            return self.objects[validate_key(key)]
        except KeyError as exc:
            raise ObjectNotFound(key) from exc

    async def delete(self, key: str) -> None:
        self.objects.pop(validate_key(key), None)

    async def exists(self, key: str) -> bool:
        return validate_key(key) in self.objects
