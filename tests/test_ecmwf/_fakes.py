"""Shared in-memory fakes for the ECMWF test suite.

Pulled out of the per-test files so each split file has the same
mocking primitives without copy-pasting. None of these are pytest
fixtures — they are plain helper classes / functions imported where
needed.
"""

from __future__ import annotations


class _SentinelClient:
    """Stand-in for :class:`cdsapi.Client` used in initialize tests.

    Empty by design — tests that need to assert "the constructed
    client is exactly this one" use `is` identity comparison
    against an instance of this class.
    """


def captured_request(stub):
    """Return the request dict from the most recent `client.retrieve` call.

    Args:
        stub: An `ECMWF` stub whose `client` is a `MagicMock`.

    Returns:
        dict: The `request` positional argument passed to
        `client.retrieve(dataset, request, target)`.
    """
    return stub.client.retrieve.call_args[0][1]
