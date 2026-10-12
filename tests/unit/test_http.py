"""HTTP client against a scripted transport: no sockets, no sleeping."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from playlist_player.errors import HttpError, NotFoundError
from playlist_player.http import USER_AGENT, HttpClient


class Script:
    """Replays a list of responses and records the requests it was sent."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.slept = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def client(self, **kwargs) -> HttpClient:
        return HttpClient(transport=self, sleep=self.slept.append, backoff_s=1.0, **kwargs)


def http_error(code, body=b""):
    return urllib.error.HTTPError("http://x", code, "msg", {}, io.BytesIO(body))


def test_get_json_builds_the_query_and_sets_a_user_agent():
    script = Script((200, b'{"ok": true}'))
    result = script.client().get_json("http://h/api", params={"a": "x y", "b": None, "n": 3})
    assert result == {"ok": True}
    request = script.requests[0]
    assert request.full_url == "http://h/api?a=x+y&n=3"  # None dropped, space encoded
    assert request.get_header("User-agent") == USER_AGENT


def test_post_json_sends_a_json_body_and_caller_headers():
    script = Script((201, b"[]"))
    script.client().post_json("http://h/run", {"k": [1]}, headers={"Authorization": "Bearer t"})
    request = script.requests[0]
    assert request.get_method() == "POST"
    assert json.loads(request.data) == {"k": [1]}
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Authorization") == "Bearer t"


def test_retries_transient_statuses_with_growing_backoff():
    script = Script(http_error(429), (503, b""), (200, b"{}"))
    client = script.client()
    assert client.get_json("http://h/") == {}
    assert script.slept == [1.0, 2.0]
    assert client.request_count == 3


def test_gives_up_after_the_retry_budget():
    script = Script((500, b"a"), (500, b"b"), (500, b"server exploded"))
    with pytest.raises(HttpError) as info:
        script.client().get_json("http://h/")
    assert info.value.status == 500 and "server exploded" in info.value.detail


def test_404_is_not_retried_and_has_its_own_type():
    script = Script(http_error(404))
    with pytest.raises(NotFoundError):
        script.client().get_json("http://h/missing")
    assert len(script.requests) == 1 and script.slept == []


def test_client_errors_are_not_retried():
    script = Script(http_error(401, b'{"error":"bad token"}'))
    with pytest.raises(HttpError) as info:
        script.client().get_json("http://h/")
    assert info.value.status == 401 and len(script.requests) == 1


def test_network_errors_retry_then_surface():
    script = Script(urllib.error.URLError("refused"), urllib.error.URLError("refused again"))
    with pytest.raises(HttpError) as info:
        script.client(retries=2).get_json("http://h/")
    assert info.value.status is None and "refused again" in str(info.value)


def test_errors_never_carry_the_query_string_or_headers():
    """A token can travel in either; neither may reach a log line or traceback."""
    script = Script(http_error(403, b"denied"))
    with pytest.raises(HttpError) as info:
        script.client().get_json(
            "http://h/api",
            params={"token": "SECRET-IN-QUERY"},
            headers={"Authorization": "Bearer SECRET-IN-HEADER"},
        )
    assert "SECRET" not in str(info.value) and "SECRET" not in repr(info.value.__dict__)
    assert info.value.url == "http://h/api"


def test_non_json_success_body_is_an_error():
    with pytest.raises(HttpError, match="not JSON"):
        Script((200, b"<html>")).client().get_json("http://h/")


def test_download_hands_bytes_to_the_writer(tmp_path):
    written = {}
    size = (
        Script((200, b"\xff\xd8jpeg"))
        .client()
        .download(
            "http://h/c.jpg", tmp_path / "c.jpg", lambda path, data: written.update({path: data})
        )
    )
    assert size == 6 and written == {tmp_path / "c.jpg": b"\xff\xd8jpeg"}
