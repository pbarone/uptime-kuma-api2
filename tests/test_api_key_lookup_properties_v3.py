"""
Property tests for get_api_key() lookup. No live server: connect and
socketio.Client are patched and the getAPIKeyList call is faked.

Property 5 (this task, 5.4): found by string id, whatever the argument type.
Property 6 (task 5.5, absent id raises) goes in this file and reuses the
strategies and make_api() below.
"""
import copy
import string

import pytest
from hypothesis import given, settings, strategies as st
from unittest.mock import MagicMock, patch

from uptime_kuma_api import UptimeKumaApi, UptimeKumaException

URL = "http://kuma.invalid:3001"

# --- shared strategies -------------------------------------------------------

# Canonical numeric-looking ids ("12"): no leading zeros, so str(int(s)) == s.
numeric_ids = st.integers(min_value=0, max_value=10 ** 9).map(str)
# Better Auth style random ids. A letter is forced in so they are never numeric.
random_ids = st.text(
    alphabet=string.ascii_letters + string.digits, min_size=2, max_size=24
).map(lambda s: "k" + s)
legacy_ids = st.integers(min_value=1, max_value=10 ** 6).map(lambda n: "legacy-%d" % n)
key_ids = st.one_of(numeric_ids, random_ids, legacy_ids)


def _record(id_):
    return {
        "id": id_,
        "name": "key-" + id_,
        "start": "uk_",
        "createdAt": "2026-03-20T11:15:05.000Z",
        "enabled": True,
        "expiresAt": None,
    }


# Non-empty list of raw server records with distinct string ids.
key_lists = st.lists(key_ids, min_size=1, max_size=12, unique=True).map(
    lambda ids: [_record(i) for i in ids]
)


# --- shared client factory ---------------------------------------------------

def make_api(records):
    """Client whose getAPIKeyList returns a fresh copy of records on each call
    (get_api_keys mutates the dicts it receives)."""
    with patch("uptime_kuma_api.api.socketio.Client") as client_cls, \
            patch.object(UptimeKumaApi, "connect"):
        client_cls.return_value = MagicMock()
        api = UptimeKumaApi(URL)
    api._call = MagicMock(
        side_effect=lambda event, *a, **kw: {"apiKeyList": copy.deepcopy(records)}
    )
    return api


# --- Property 5 --------------------------------------------------------------

# Property 5: get_api_key finds the key matching as a string regardless of the
# id argument's type.
# Validates: Requirements 10.4, 11.5
@settings(max_examples=200, deadline=None)
@given(records=key_lists, data=st.data())
def test_property_5_get_api_key_matches_as_string(records, data):
    api = make_api(records)
    target = data.draw(st.sampled_from(records))["id"]

    by_str = api.get_api_key(target)
    assert by_str["id"] == target

    if target.isdigit():
        by_int = api.get_api_key(int(target))
        assert by_int["id"] == target
        assert by_int == by_str


# --- Property 6 --------------------------------------------------------------

# Property 6: get_api_key raises UptimeKumaException for an id (string or int)
# that no key has as a string; delete_api_key raises the same and never emits
# deleteAPIKey.
# Validates: Requirements 10.5
absent_candidates = st.one_of(
    st.text(max_size=30),
    st.integers(min_value=-10 ** 9, max_value=10 ** 9),
    key_ids,
)


@settings(max_examples=200, deadline=None)
@given(records=key_lists, data=st.data())
def test_property_6_absent_id_raises(records, data):
    present = {r["id"] for r in records}
    absent = data.draw(absent_candidates.filter(lambda x: str(x) not in present))
    api = make_api(records)

    with pytest.raises(UptimeKumaException, match="api key does not exist"):
        api.get_api_key(absent)

    with pytest.raises(UptimeKumaException, match="api key does not exist"):
        api.delete_api_key(absent)
    events = [c.args[0] for c in api._call.call_args_list if c.args]
    assert "deleteAPIKey" not in events
