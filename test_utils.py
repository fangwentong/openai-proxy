import httpx
import pytest

import main as proxy_main
from utils import PathMatchingTree


def test_get_matching_exact_match():
    config = {
        "foo/bar": "value1",
        "baz/qux": "value2",
    }
    pmt = PathMatchingTree(config)
    assert pmt.get_matching("foo/bar") == "value1"


def test_get_matching_partial_match():
    config = {
        "foo/bar": "value1",
        "baz/qux": "value2",
    }
    pmt = PathMatchingTree(config)
    assert pmt.get_matching("foo") is None


def test_get_matching_wildcard_match():
    config = {
        "/foo/*": "value1",
        "/baz/qux": "value2",
    }
    pmt = PathMatchingTree(config)
    assert pmt.get_matching("foo/bar") == "value1"


def test_get_matching_multiple_wildcard_match():
    config = {
        "/foo/*": "value1",
        "/foo/*/bar": "value2",
    }
    pmt = PathMatchingTree(config)
    assert pmt.get_matching("/foo") is None
    assert pmt.get_matching("/foo/baz") == "value1"
    assert pmt.get_matching("/foo/baz/bar2") == "value1"
    assert pmt.get_matching("/foo/baz/bar") == "value2"


def test_get_matching_no_match():
    config = {
        "/foo/bar": "value1",
        "/baz/qux": "value2",
    }
    pmt = PathMatchingTree(config)
    assert pmt.get_matching("/foo") is None
    assert pmt.get_matching("/baz") is None


def test_get_matching_empty_string_match():
    config = {
        "/": "value1",
    }
    pmt = PathMatchingTree(config)
    assert pmt.get_matching("/") == "value1"
    assert pmt.get_matching("/test") == "value1"


def test_get_matching_root_match():
    config = {
        "/": "value1",
    }
    pmt = PathMatchingTree(config)
    assert pmt.get_matching("/") == "value1"


def test_get_matching_longest_prefix_fallback_to_root():
    config = {
        "/": "https://api.openai.com/",
        "/backend-api/conversation": "https://chat.openai.com/",
    }
    pmt = PathMatchingTree(config)
    assert pmt.get_matching("/v1/chat/completions") == "https://api.openai.com"
    assert pmt.get_matching("/backend-api") == "https://api.openai.com"
    assert pmt.get_matching("/backend-api/other") == "https://api.openai.com"
    assert pmt.get_matching("/backend-api/conversation") == "https://chat.openai.com"
    assert pmt.get_matching("/backend-api/conversation/123") == "https://chat.openai.com"


def test_get_matching_wildcard_backtracking():
    config = {
        "/foo/bar/baz": "exact_deep",
        "/foo/*": "wildcard_shallow",
    }
    pmt = PathMatchingTree(config)
    assert pmt.get_matching("/foo/bar/baz") == "exact_deep"
    assert pmt.get_matching("/foo/bar/qux") == "wildcard_shallow"
    assert pmt.get_matching("/foo/other") == "wildcard_shallow"


def test_get_matching_exact_wins_over_wildcard_with_same_value_as_root():
    config = {
        "/": "same_val",
        "/foo/bar": "same_val",
        "/foo/*": "wildcard_val",
    }
    pmt = PathMatchingTree(config)
    assert pmt.get_matching("/foo/bar") == "same_val"
    assert pmt.get_matching("/foo/baz") == "wildcard_val"


def test_get_matching_deeper_wildcard_wins_over_shallower_exact():
    config = {
        "/foo/bar": "shallow_exact",
        "/foo/*/baz": "deep_wildcard",
    }
    pmt = PathMatchingTree(config)
    assert pmt.get_matching("/foo/bar/baz") == "deep_wildcard"
    assert pmt.get_matching("/foo/bar/other") == "shallow_exact"


def test_get_matching_empty_config_and_trailing_slash():
    assert PathMatchingTree({}).get_matching("/") is None
    assert PathMatchingTree({}).get_matching("/v1/models") is None
    assert PathMatchingTree({"/": "https://api.openai.com///"}).get_matching("/v1/models") == "https://api.openai.com"


@pytest.mark.asyncio
async def test_unmatched_host_returns_404(monkeypatch):
    monkeypatch.setattr(proxy_main, "proxied_hosts", PathMatchingTree({"/only/here": "https://api.openai.com"}))
    transport = httpx.ASGITransport(app=proxy_main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/unmatched/path")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "No matching upstream host"
