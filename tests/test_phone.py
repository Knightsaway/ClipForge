"""Phone auth helpers."""
from __future__ import annotations

from clipforge.phone import _token_ok, new_token


def test_token_ok_and_length_mismatch():
    t = new_token()
    assert _token_ok(t, t)
    assert not _token_ok("wrong", t)
    assert not _token_ok("", t)
    assert not _token_ok(t + "x", t)  # different length must not raise
