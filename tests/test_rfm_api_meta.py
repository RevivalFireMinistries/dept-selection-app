"""The paginated envelope's `meta` has to survive being unwrapped.

rfm-database answers `{"data": [...], "meta": {"total": 23, ...}}`. The
client unwraps `data` so callers get the list they asked for — and it used
to drop `meta` on the floor. Nothing raised: on a list response
`r.data.get("meta")` is not even reachable, so every count read that way
silently stayed 0.

That is what put five zeroes across the top of the Kingdom Gateway
dashboard while the list underneath it showed 23 people.
"""
import json
from unittest.mock import patch

import pytest

import rfm_api_client as rfm


class _FakeResponse:
    """Stand-in for urlopen's context manager."""

    def __init__(self, payload, status=200):
        self._body = json.dumps(payload).encode()
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _call(payload, status=200):
    with patch.object(rfm, "is_enabled", return_value=True), \
         patch.object(rfm, "get_api_url", return_value="http://rfm-db.test"), \
         patch.object(rfm, "get_api_key", return_value="k"), \
         patch("urllib.request.urlopen", return_value=_FakeResponse(payload, status)):
        return rfm._request("GET", "/api/v1/members")


PAGINATED = {
    "data": [{"id": "1", "first_name": "Sipho"}],
    "meta": {"total": 23, "page": 1, "size": 1, "pages": 23, "cached": False},
}


def test_the_envelope_meta_survives_the_unwrap():
    r = _call(PAGINATED)
    assert r.ok
    assert r.meta == PAGINATED["meta"]


def test_data_is_still_the_unwrapped_payload():
    """Callers that only want the rows keep getting exactly the rows."""
    r = _call(PAGINATED)
    assert r.data == PAGINATED["data"]
    assert isinstance(r.data, list)


def test_the_total_is_readable_from_a_list_response():
    """The actual regression: a count taken off a LIST response. Reading it
    from `data` could never work, and never said so."""
    r = _call(PAGINATED)
    assert (r.meta or {}).get("total", 0) == 23


def test_a_response_with_no_meta_leaves_it_none():
    r = _call({"data": {"id": "1"}})
    assert r.ok
    assert r.meta is None
    assert r.data == {"id": "1"}


def test_an_unenveloped_response_still_comes_back():
    r = _call({"status": "ok"})
    assert r.ok
    assert r.data == {"status": "ok"}
    assert r.meta is None


def test_a_non_dict_meta_is_ignored_rather_than_trusted():
    """Callers do `(r.meta or {}).get(...)`. Handing them a string would
    turn a bad response into an AttributeError somewhere else entirely."""
    r = _call({"data": [], "meta": "unexpected"})
    assert r.ok
    assert r.meta is None


@pytest.mark.parametrize("disabled_switch", [True])
def test_a_disabled_integration_has_no_meta(disabled_switch):
    with patch.object(rfm, "is_enabled", return_value=False):
        r = rfm._request("GET", "/api/v1/members")
    assert not r.ok and r.disabled
    assert r.meta is None
