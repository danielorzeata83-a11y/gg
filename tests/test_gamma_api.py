"""Tests for gamma_api: parsing, index building, cache, metadata gates."""
import json
import time
from datetime import datetime, timezone, timedelta
import pytest

import gamma_api
from gamma_api import (
    _parse_json_field, _to_float, build_index, MarketMeta, GammaCache,
)


# ---- Parsing helpers --------------------------------------------------------

def test_parse_stringified_clob_token_ids():
    # Gamma returns clobTokenIds as a JSON string, not a list
    raw = '["111", "222"]'
    assert _parse_json_field(raw) == ["111", "222"]


def test_parse_already_list():
    assert _parse_json_field(["a", "b"]) == ["a", "b"]


def test_parse_garbage_returns_empty():
    assert _parse_json_field("not json") == []
    assert _parse_json_field(None) == []
    assert _parse_json_field(42) == []


def test_to_float_robust():
    assert _to_float("3.5") == 3.5
    assert _to_float(None) == 0.0
    assert _to_float("bad") == 0.0
    assert _to_float(7) == 7.0


# ---- Index building ---------------------------------------------------------

def _market(token_ids, **kw):
    m = {
        "clobTokenIds": json.dumps(token_ids),
        "conditionId": kw.get("conditionId", "0xcond"),
        "question": kw.get("question", "Will X happen?"),
        "slug": kw.get("slug", "will-x"),
        "liquidityNum": kw.get("liquidity", 5000.0),
        "volumeNum": kw.get("volume", 12000.0),
        "volume24hr": kw.get("volume24hr", 800.0),
        "spread": kw.get("spread", 0.02),
        "endDate": kw.get("endDate", "2026-12-31T00:00:00Z"),
        "active": kw.get("active", True),
        "closed": kw.get("closed", False),
    }
    if "events" in kw:
        m["events"] = kw["events"]
    return m


def test_build_index_both_tokens_point_to_market():
    markets = [_market(["111", "222"])]
    idx = build_index(markets)
    assert set(idx.keys()) == {"111", "222"}
    assert idx["111"].condition_id == "0xcond"
    assert idx["111"].liquidity == 5000.0
    assert idx["222"].question == "Will X happen?"


def test_build_index_skips_market_without_tokens():
    markets = [{"clobTokenIds": "[]", "conditionId": "x"}]
    idx = build_index(markets)
    assert idx == {}


def test_tags_from_embedded_events():
    markets = [_market(["111", "222"], events=[
        {"id": "ev1", "tags": [{"label": "Politics"}, {"label": "US"}]}
    ])]
    idx = build_index(markets)
    assert idx["111"].tags == ["Politics", "US"]
    assert idx["111"].event_id == "ev1"


def test_tags_joined_from_separate_events():
    markets = [_market(["111", "222"], events=[{"id": "ev9", "tags": []}])]
    events = [{"id": "ev9", "tags": [{"label": "Crypto"}]}]
    idx = build_index(markets, events)
    assert idx["111"].tags == ["Crypto"]


def test_liquidity_fallback_to_non_num_field():
    m = _market(["111", "222"])
    del m["liquidityNum"]
    m["liquidity"] = 333.0
    idx = build_index([m])
    assert idx["111"].liquidity == 333.0


# ---- hours_to_resolution ----------------------------------------------------

def test_hours_to_resolution_future():
    future = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    meta = MarketMeta(token_id="1", end_date=future)
    hrs = meta.hours_to_resolution()
    assert 47 < hrs < 49


def test_hours_to_resolution_unknown():
    meta = MarketMeta(token_id="1", end_date="")
    assert meta.hours_to_resolution() is None


def test_hours_to_resolution_past_is_negative():
    past = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    meta = MarketMeta(token_id="1", end_date=past)
    assert meta.hours_to_resolution() < 0


# ---- Cache round-trip -------------------------------------------------------

def test_cache_save_load_roundtrip(tmp_path):
    path = str(tmp_path / "gamma.json")
    cache = GammaCache(path)
    cache._index = build_index([_market(["111", "222"], events=[
        {"id": "ev1", "tags": [{"label": "Sports"}]}
    ])])
    cache.built_at = time.time()
    cache.save()

    fresh = GammaCache(path)
    assert fresh.load() is True
    meta = fresh.lookup("111")
    assert meta is not None
    assert meta.liquidity == 5000.0
    assert meta.tags == ["Sports"]
    assert len(fresh) == 2


def test_cache_load_missing_file(tmp_path):
    cache = GammaCache(str(tmp_path / "nope.json"))
    assert cache.load() is False
    assert cache.lookup("111") is None  # graceful


def test_cache_load_corrupt_file(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not valid json")
    cache = GammaCache(str(path))
    assert cache.load() is False


# ---- refresh() uses fetch_* (mocked, no network) ---------------------------

def test_refresh_builds_index(monkeypatch):
    monkeypatch.setattr(gamma_api, "fetch_markets",
                        lambda **kw: [_market(["111", "222"])])
    monkeypatch.setattr(gamma_api, "fetch_events",
                        lambda **kw: [])
    cache = GammaCache("unused.json")
    n = cache.refresh()
    assert n == 2
    assert cache.lookup("222").volume == 12000.0
