import threading
import time

import pytest

from monitor.collector import Collector, IMAGE_FS, Sampler, Thresholds, rate


def test_rate_from_counters():
    assert rate(1000, 3000, 2) == 1000
    assert rate(None, 3000, 2) == 0
    assert rate(1000, 3000, 0) == 0


def test_counter_reset_counts_as_zero_not_negative():
    assert rate(5000, 100, 1) == 0


@pytest.mark.parametrize("value,level", [(0, "ok"), (74.9, "ok"), (75, "warn"), (89.9, "warn"), (90, "crit"), (100, "crit")])
def test_threshold_levels(value, level):
    assert Thresholds().level(value) == level


def test_sample_has_the_shape_the_dashboard_reads():
    c = Collector(top_n=3)
    c.sample()
    s = c.sample()
    assert 0 <= s["cpu"]["percent"] <= 100 and len(s["cpu"]["cores"]) == c.host["cores"]
    assert s["memory"]["total"] > s["memory"]["used"] >= 0
    assert s["net"]["in"] >= 0 and s["diskio"]["write"] >= 0
    assert len(s["processes"]) <= 3
    assert all(set(d) >= {"mount", "percent", "status", "readonly"} for d in s["disks"])
    assert s["uptime"] > 0


def test_image_filesystems_never_appear_as_disks():
    s = Collector().sample()
    assert not any(d["fs"] in IMAGE_FS for d in s["disks"])


def test_read_only_mounts_never_raise_alerts():
    c = Collector(Thresholds(0.1, 0.2))
    s = c.sample()
    assert not any(a["metric"].startswith("disk:") and any(d["mount"] == a["metric"][5:] and d["readonly"] for d in s["disks"]) for a in s["alerts"])


def test_alerts_follow_thresholds():
    c = Collector(Thresholds(0.0001, 0.0002))
    c.sample()
    alerts = c.sample()["alerts"]
    assert any(a["metric"] == "memory" and a["level"] == "crit" for a in alerts)
    assert all(a["message"] for a in alerts)


def test_no_processes_option_hides_names():
    assert Collector(include_processes=False).sample()["processes"] == []


def test_sampler_keeps_a_bounded_history_and_wakes_waiters():
    sampler = Sampler(Collector(include_processes=False), interval=0.05, history=4)
    sampler.start()
    try:
        first = sampler.latest()["seq"]
        nxt = sampler.wait_for(first, timeout=2)
        assert nxt is not None and nxt["seq"] > first
        time.sleep(0.4)
        h = sampler.history()
        assert len(h) == 4 and [x["seq"] for x in h] == sorted(x["seq"] for x in h)
        assert sampler.wait_for(10**9, timeout=0.05) is None
    finally:
        sampler.stop()


def test_sampler_survives_a_failing_sample():
    class Boom(Collector):
        calls = 0

        def sample(self):
            Boom.calls += 1
            if Boom.calls == 2:
                raise OSError("disk vanished")
            return super().sample()

    s = Sampler(Boom(include_processes=False), interval=0.05, history=10)
    s.start()
    try:
        time.sleep(0.4)
        assert Boom.calls >= 3 and s.latest()["seq"] >= 2
    finally:
        s.stop()
