from __future__ import annotations

import os
import platform
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

import psutil


@dataclass(frozen=True)
class Thresholds:
    warn: float = 75.0
    crit: float = 90.0

    def level(self, value: float) -> str:
        return "crit" if value >= self.crit else "warn" if value >= self.warn else "ok"


# Image filesystems are full by design, so they would raise a permanent false alarm.
IMAGE_FS = frozenset({"squashfs", "iso9660", "udf", "cramfs", "erofs", "overlay", "tmpfs", "devtmpfs"})


def rate(prev: float | None, cur: float, dt: float) -> float:
    """Per-second rate from two counter readings; a counter that went backwards (reset or wrap) counts as zero."""
    if prev is None or dt <= 0 or cur < prev:
        return 0.0
    return (cur - prev) / dt


@dataclass
class Collector:
    thresholds: Thresholds = field(default_factory=Thresholds)
    include_processes: bool = True
    top_n: int = 8
    clock: Callable[[], float] = time.time
    _prev: dict[str, float] = field(default_factory=dict)
    _prev_t: float | None = None
    _procs: dict[int, psutil.Process] = field(default_factory=dict)

    def __post_init__(self) -> None:
        psutil.cpu_percent(interval=None, percpu=True)
        self.host = {
            "hostname": socket.gethostname(),
            "os": f"{platform.system()} {platform.release()}",
            "cores": psutil.cpu_count(logical=True) or 1,
            "boot_time": psutil.boot_time(),
        }

    def _processes(self) -> list[dict[str, Any]]:
        live: dict[int, psutil.Process] = {}
        rows = []
        for p in psutil.process_iter():
            proc = self._procs.get(p.pid, p)
            live[p.pid] = proc
            try:
                with proc.oneshot():
                    cpu = proc.cpu_percent(interval=None)
                    rows.append({"pid": proc.pid, "name": proc.name()[:40], "cpu": round(cpu, 1),
                                 "mem": round(proc.memory_percent(), 1)})
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                live.pop(p.pid, None)
        self._procs = live
        rows.sort(key=lambda r: (r["cpu"], r["mem"]), reverse=True)
        return rows[: self.top_n]

    def sample(self) -> dict[str, Any]:
        now = self.clock()
        dt = now - self._prev_t if self._prev_t else 0.0
        cores = psutil.cpu_percent(interval=None, percpu=True)
        cpu = sum(cores) / len(cores) if cores else 0.0
        vm, sw = psutil.virtual_memory(), psutil.swap_memory()

        disks = []
        seen: set[str] = set()
        for part in psutil.disk_partitions(all=False):
            if part.mountpoint in seen or part.fstype in IMAGE_FS:
                continue
            seen.add(part.mountpoint)
            try:
                u = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            readonly = "ro" in part.opts.split(",")
            disks.append({"mount": part.mountpoint, "fs": part.fstype, "total": u.total, "used": u.used, "percent": u.percent,
                          "readonly": readonly, "status": "ok" if readonly else self.thresholds.level(u.percent)})

        counters: dict[str, float] = {}
        net = psutil.net_io_counters()
        if net:
            counters.update(net_in=net.bytes_recv, net_out=net.bytes_sent)
        dio = psutil.disk_io_counters()
        if dio:
            counters.update(disk_read=dio.read_bytes, disk_write=dio.write_bytes)
        rates = {k: rate(self._prev.get(k), v, dt) for k, v in counters.items()}
        self._prev, self._prev_t = counters, now

        try:
            load = list(os.getloadavg())
        except (AttributeError, OSError):
            load = None

        snap: dict[str, Any] = {
            "t": now,
            "cpu": {"percent": round(cpu, 1), "cores": [round(c, 1) for c in cores], "status": self.thresholds.level(cpu), "load": load},
            "memory": {"percent": vm.percent, "used": vm.total - vm.available, "total": vm.total, "swap_percent": sw.percent,
                       "status": self.thresholds.level(vm.percent)},
            "disks": disks,
            "net": {"in": rates.get("net_in", 0.0), "out": rates.get("net_out", 0.0)},
            "diskio": {"read": rates.get("disk_read", 0.0), "write": rates.get("disk_write", 0.0)},
            "uptime": now - self.host["boot_time"],
            "processes": self._processes() if self.include_processes else [],
        }
        snap["alerts"] = self.alerts(snap)
        return snap

    def alerts(self, s: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for name, label, value in [("cpu", "CPU", s["cpu"]["percent"]), ("memory", "Memory", s["memory"]["percent"])] + \
                [("disk:" + d["mount"], f"Disk {d['mount']}", d["percent"]) for d in s["disks"] if not d["readonly"]]:
            level = self.thresholds.level(value)
            if level != "ok":
                out.append({"metric": name, "level": level, "value": value,
                            "message": f"{label} is at {value:.0f}% (limit {self.thresholds.crit if level == 'crit' else self.thresholds.warn:.0f}%)"})
        return out


class Sampler:
    """Samples on a background thread and keeps the last `history` snapshots; readers wait for new ones."""

    def __init__(self, collector: Collector, interval: float = 2.0, history: int = 150):
        self.collector = collector
        self.interval = interval
        self.buffer: deque[dict[str, Any]] = deque(maxlen=history)
        self.seq = 0
        self.cond = threading.Condition()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="sampler", daemon=True)

    def start(self) -> None:
        self._push(self.collector.sample())
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self.cond:
            self.cond.notify_all()
        self._thread.join(timeout=5)

    def _push(self, snap: dict[str, Any]) -> None:
        with self.cond:
            self.seq += 1
            snap["seq"] = self.seq
            self.buffer.append(snap)
            self.cond.notify_all()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self._push(self.collector.sample())
            except Exception:
                continue

    def latest(self) -> dict[str, Any]:
        with self.cond:
            return self.buffer[-1]

    def history(self) -> list[dict[str, Any]]:
        with self.cond:
            return list(self.buffer)

    def wait_for(self, after_seq: int, timeout: float) -> dict[str, Any] | None:
        with self.cond:
            self.cond.wait_for(lambda: self.seq > after_seq or self._stop.is_set(), timeout)
            return self.buffer[-1] if self.seq > after_seq else None
