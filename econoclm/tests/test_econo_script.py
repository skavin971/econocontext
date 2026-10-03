"""The `econo` POSIX sh tool: on the host's /bin/sh, and in alpine (busybox, no
Python) and python:3.12-slim (Python, no sqlite3 CLI) containers when Docker runs."""

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "arms/econo_clm/skill/econo_db/econo"

# Awkward bytes on purpose: tabs, unicode, CRLF, a backslash, no final newline.
OUT1 = "line one\nsecond\tTAB line\nnaïve UTF-8 ✓\r\nback\\slash -n -e\nlast line no newline"
OUT2 = "\n".join(f"row {i}: value {i * i}" for i in range(1, 301)) + "\n"


def make_store(root: Path) -> Path:
    d = root / "econo"
    (d / "obs").mkdir(parents=True)
    (d / "obs/1.txt").write_bytes(OUT1.encode())
    (d / "obs/2.txt").write_bytes(OUT2.encode())
    (d / "index.tsv").write_text(f"1\t1\t{len(OUT1.encode())}\tcat notes.txt\n"
                                 f"2\t2\t{len(OUT2.encode())}\tpython gen.py\n")
    (d / "log.tsv").write_text("")
    return d


def sha1(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


class HostRunner:
    def __init__(self, store: Path):
        self.store = store

    def __call__(self, *args):
        return subprocess.run(["sh", str(SCRIPT), *args], capture_output=True,
                              env={"ECONO_DIR": str(self.store), "PATH": "/usr/bin:/bin"})


class DockerRunner:
    def __init__(self, store: Path, image: str):
        self.store, self.image = store, image

    def __call__(self, *args):
        return subprocess.run(
            ["docker", "run", "--rm", "--network", "none",
             "-v", f"{self.store}:/tmp/econo", "-v", f"{SCRIPT}:/usr/local/bin/econo:ro",
             self.image, "sh", "/usr/local/bin/econo", *args], capture_output=True)


def docker_ok(image: str) -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode == 0 \
        or subprocess.run(["docker", "pull", "-q", image], capture_output=True).returncode == 0


PARAMS = [pytest.param(None, id="host-sh"),
          pytest.param("alpine:latest", id="alpine"),
          pytest.param("python:3.12-slim", id="python-slim")]


@pytest.fixture(params=PARAMS)
def run(request, tmp_path):
    store = make_store(tmp_path)
    if request.param is None:
        return HostRunner(store), store, None
    if not docker_ok(request.param):
        pytest.skip(f"docker image {request.param} not available")
    return DockerRunner(store, request.param), store, request.param


def test_get_is_byte_identical(run):
    econo, store, _ = run
    for n in ("1", "2"):
        r = econo("get", n)
        assert r.returncode == 0
        assert sha1(r.stdout) == sha1((store / f"obs/{n}.txt").read_bytes())


def test_get_range(run):
    econo, _, _ = run
    r = econo("get", "2", "200-202")
    assert r.returncode == 0
    assert r.stdout == b"row 200: value 40000\nrow 201: value 40401\nrow 202: value 40804\n"


def test_list(run):
    econo, _, _ = run
    r = econo("list")
    lines = r.stdout.decode().splitlines()
    assert lines[0] == "id  turn  bytes  command"
    assert lines[2].startswith("2  2  ") and lines[2].endswith("python gen.py")


def test_search(run):
    econo, _, _ = run
    r = econo("search", "TAB", "line")
    assert r.returncode == 0
    assert r.stdout.decode().splitlines() == ["obs 1:2:second\tTAB line"]
    many = econo("search", "value").stdout.decode().splitlines()
    assert len(many) == 50 and many[0] == "obs 2:1:row 1: value 1"
    assert econo("search", "zzz-not-there").stdout == b"(no matches)\n"


def test_errors_exit_1(run):
    econo, _, _ = run
    for args in [("get", "9"), ("get", "x"), ("get", "1", "5-2"), ("get", "1", "abc"),
                 ("get",), ("bogus",), ()]:
        r = econo(*args)
        assert r.returncode == 1, args
        assert r.stderr.startswith(b"econo: ") or b"usage" in r.stderr


def test_sql_and_log(run):
    econo, store, image = run
    w = econo("sql", "CREATE TABLE notes(k TEXT, v TEXT)")
    if image == "alpine:latest":   # no sqlite3, no python3
        assert w.returncode == 1 and w.stdout == b"sql not available in this container\n"
    else:
        has_sql = shutil.which("sqlite3") or shutil.which("python3")
        if image is None and not has_sql:
            pytest.skip("host has neither sqlite3 nor python3")
        assert w.returncode == 0, w.stderr
        assert econo("sql", "INSERT INTO notes VALUES('bug', 'parser.py:142')").returncode == 0
        r = econo("sql", "SELECT k, v FROM notes")
        assert r.stdout.decode().strip() == "bug|parser.py:142"
    econo("get", "1")
    ops = [ln.split("\t") for ln in (store / "log.tsv").read_text().splitlines()]
    names = [o[1] for o in ops]
    assert names[0] == "sql_write" and names[-1] == "get"
    assert all(o[0].isdigit() for o in ops)
    if image != "alpine:latest":
        assert names[:3] == ["sql_write", "sql_write", "sql_read"]
