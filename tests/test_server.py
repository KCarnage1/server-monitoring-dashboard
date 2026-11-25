import json
import threading
import urllib.error
import urllib.request

import pytest

from monitor.cli import main
from monitor.collector import Collector, Sampler
from monitor.server import MonitorServer


@pytest.fixture
def make_server():
    started = []

    def make(token=None, extra_hosts=None):
        sampler = Sampler(Collector(include_processes=False), interval=0.2, history=20)
        sampler.start()
        server = MonitorServer(("127.0.0.1", 0), sampler, token, extra_hosts)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        started.append((server, sampler))
        return server, f"http://127.0.0.1:{server.server_address[1]}"

    yield make
    for server, sampler in started:
        server.shutdown()
        server.server_close()
        sampler.stop()


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_pages_and_api_work(make_server):
    _, base = make_server()
    status, headers, body = get(base + "/")
    assert status == 200 and b"Server monitor" in body
    assert "default-src 'none'" in headers["Content-Security-Policy"] and headers["X-Content-Type-Options"] == "nosniff"
    for path in ("/app.js", "/lib.js", "/style.css"):
        assert get(base + path)[0] == 200
    snap = json.loads(get(base + "/api/snapshot")[2])
    assert "cpu" in snap and snap["seq"] >= 1
    assert json.loads(get(base + "/api/info")[2])["hostname"]
    assert len(json.loads(get(base + "/api/history")[2])) >= 1


def test_unknown_paths_and_traversal_are_404(make_server):
    _, base = make_server()
    for path in ("/nope", "/api/nope", "/../pyproject.toml", "/%2e%2e/cli.py", "/monitor/server.py"):
        assert get(base + path)[0] == 404


def test_token_is_required_when_set(make_server):
    _, base = make_server(token="s3cret")
    assert get(base + "/api/snapshot")[0] == 401
    assert get(base + "/api/snapshot?token=wrong")[0] == 401
    assert get(base + "/api/snapshot?token=s3cret")[0] == 200
    assert get(base + "/api/snapshot", {"Authorization": "Bearer s3cret"})[0] == 200
    assert get(base + "/")[0] == 200


def test_foreign_host_header_is_refused_without_a_token(make_server):
    _, base = make_server()
    assert get(base + "/api/snapshot", {"Host": "evil.example"})[0] == 403
    assert get(base + "/api/snapshot", {"Host": "localhost:9999"})[0] == 200


def test_allow_host_opens_a_specific_name(make_server):
    _, base = make_server(extra_hosts={"monitor.lan"})
    assert get(base + "/api/snapshot", {"Host": "monitor.lan:8765"})[0] == 200


def test_event_stream_sends_new_readings(make_server):
    _, base = make_server()
    with urllib.request.urlopen(base + "/api/stream", timeout=5) as r:
        assert r.headers["Content-Type"] == "text/event-stream"
        seen = []
        while len(seen) < 2:
            line = r.readline().decode()
            if line.startswith("data: "):
                seen.append(json.loads(line[6:])["seq"])
    assert seen[1] > seen[0]


def test_cli_refuses_a_public_bind_without_a_token(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--host", "0.0.0.0", "--port", "0"])
    assert e.value.code == 2 and "--token" in capsys.readouterr().err


@pytest.mark.parametrize("args", [["--interval", "0.1"], ["--warn", "95", "--crit", "90"], ["--crit", "120"]])
def test_cli_rejects_bad_numbers(args):
    with pytest.raises(SystemExit) as e:
        main(args)
    assert e.value.code == 2
