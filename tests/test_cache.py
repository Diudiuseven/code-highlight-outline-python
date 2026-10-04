"""Tests for LRU eviction and SingleFlight request coalescing."""

import threading
import time

from codeview.cache import LRUCache, SingleFlight


def test_lru_eviction_order():
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")
    cache.put("c", 3)  # evicts b
    assert "b" not in cache._data
    assert cache.get("a") == 1
    assert cache.get("c") == 3


def test_lru_zero_capacity_stores_nothing():
    cache = LRUCache(0)
    cache.put("a", 1)
    assert len(cache) == 0


def test_singleflight_merges_concurrent_calls():
    flight = SingleFlight()
    started = threading.Event()
    finish = threading.Event()
    calls = []

    def work():
        calls.append(1)
        started.set()
        finish.wait(2)
        return 42

    results = []

    def follower():
        results.append(flight.run("k", work))

    t1 = threading.Thread(target=follower)
    t2 = threading.Thread(target=follower)
    t1.start()
    started.wait(1)
    t2.start()
    time.sleep(0.05)
    finish.set()
    t1.join(1)
    t2.join(1)
    assert calls == [1]
    assert results == [42, 42]


def test_singleflight_propagates_exception_to_all():
    flight = SingleFlight()
    errors = []

    def boom():
        time.sleep(0.02)
        raise ValueError("bad")

    def call():
        try:
            flight.run("k", boom)
        except ValueError as e:
            errors.append(str(e))

    threads = [threading.Thread(target=call) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(1)
    assert errors == ["bad"] * 4
    # state cleaned up; next call computes again
    assert flight.run("k2", lambda: "fresh") == "fresh"


def test_singleflight_sequential_calls_recompute():
    flight = SingleFlight()
    counter = {"n": 0}

    def work():
        counter["n"] += 1
        return counter["n"]

    assert flight.run("k", work) == 1
    assert flight.run("k", work) == 2
