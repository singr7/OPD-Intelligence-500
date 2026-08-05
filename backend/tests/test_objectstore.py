"""Where scanned pages live (doc 21 §1.3).

The store is small enough that its whole risk surface is: does a key from a
route reach the filesystem as a path it should not, does a crash mid-write leave
a half page an extractor will read as a whole one, and can a missing object be
told apart from an empty one.
"""

from __future__ import annotations

import pytest

from app.providers.objectstore import (
    FakeObjectStore,
    FilesystemObjectStore,
    ObjectNotFound,
    ObjectStoreError,
)


@pytest.fixture
def store(tmp_path) -> FilesystemObjectStore:
    return FilesystemObjectStore(tmp_path / "records")


async def test_a_page_survives_the_round_trip(store):
    key = await store.put("records/p1/d1/page-1.jpg", b"\xff\xd8jpeg-bytes")

    assert key == "records/p1/d1/page-1.jpg"
    assert await store.get(key) == b"\xff\xd8jpeg-bytes"
    assert await store.exists(key)


async def test_a_missing_page_is_a_named_state_not_a_crash(store):
    """The doctor's Reports tab has to be able to say "this page is gone"
    (doc 21 §1.5). It can only do that if the store distinguishes it."""
    with pytest.raises(ObjectNotFound):
        await store.get("records/p1/d1/page-9.jpg")

    assert not await store.exists("records/p1/d1/page-9.jpg")


@pytest.mark.parametrize(
    "key",
    [
        "../../../etc/passwd",
        "records/../../etc/passwd",
        "/etc/passwd",
        "records/p1/",
        "records/p1/page 1.jpg",  # space: not in the generated-key alphabet
        "",
    ],
)
async def test_keys_outside_the_alphabet_are_refused(store, key):
    """Keys are always built by `app.mrd`, never by a client — and are validated
    anyway. "The caller always passes a safe key" is the assumption that turns
    one careless route into an arbitrary file read."""
    with pytest.raises(ObjectStoreError):
        await store.put(key, b"x")
    with pytest.raises(ObjectStoreError):
        await store.get(key)


async def test_a_page_is_never_visible_half_written(store, monkeypatch):
    """Atomic rename, not a streaming write. A crash mid-upload must leave *no*
    object rather than a truncated one: a half-read report page extracts into
    values that look exactly as confident as whole ones."""
    import os

    real_replace = os.replace

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        await store.put("records/p1/d1/page-1.jpg", b"half")
    monkeypatch.setattr(os, "replace", real_replace)

    assert not await store.exists("records/p1/d1/page-1.jpg")
    # And no `.partial-` litter left behind for the next restore to puzzle over.
    assert list((store._root / "records/p1/d1").glob(".partial-*")) == []


async def test_overwriting_a_key_replaces_it(store):
    await store.put("records/p1/d1/page-1.jpg", b"first")
    await store.put("records/p1/d1/page-1.jpg", b"second")

    assert await store.get("records/p1/d1/page-1.jpg") == b"second"


async def test_pages_are_written_owner_only(store):
    """Clinical records on a shared box. 0600 under 0700."""
    await store.put("records/p1/d1/page-1.jpg", b"x")

    path = store._root / "records/p1/d1/page-1.jpg"
    assert oct(path.stat().st_mode)[-3:] == "600"
    assert oct(path.parent.stat().st_mode)[-3:] == "700"


async def test_deleting_something_already_gone_is_not_an_error(store):
    await store.delete("records/p1/d1/page-1.jpg")  # no raise


async def test_the_fake_and_the_real_store_agree(tmp_path):
    """The fake is what every other test runs against, so its behaviour has to
    be the real one's — including which keys it refuses."""
    fake = FakeObjectStore()
    real = FilesystemObjectStore(tmp_path)

    for store in (fake, real):
        await store.put("records/a/b/page-1.jpg", b"same bytes")
        assert await store.get("records/a/b/page-1.jpg") == b"same bytes"
        assert await store.exists("records/a/b/page-1.jpg")
        with pytest.raises(ObjectNotFound):
            await store.get("records/a/b/page-2.jpg")
        with pytest.raises(ObjectStoreError):
            await store.put("../escape", b"x")
