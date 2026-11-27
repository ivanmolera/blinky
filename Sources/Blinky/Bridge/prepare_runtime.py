"""Prepare the private Python environment used by the installable Mac app."""

from __future__ import annotations

from contextlib import contextmanager, suppress
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import stat
import subprocess
import sys
import time


_TIMEOUT = 140
_RUNTIME_SCHEMA = "1"
_DEPENDENCIES = Path(__file__).with_name("requirements.txt")
_PIP_CERTIFICATE = "import json; from pip._vendor import certifi; print(json.dumps(certifi.where()))"
_RUNTIME_CERTIFICATE = (
    "import json, ssl, aiohttp, blinkpy.auth, blinkpy.blinkpy, certifi; "
    "ssl.create_default_context(cafile=certifi.where()); print(json.dumps(certifi.where()))"
)


class UnsupportedPython(Exception):
    """The interpreter cannot create a supported Python environment."""


def _base_executable() -> Path:
    if sys.version_info < (3, 10) or any(importlib.util.find_spec(name) is None for name in ("venv", "ensurepip")):
        raise UnsupportedPython
    executable = getattr(sys, "_base_executable", None)
    if not executable or not os.path.isabs(executable) or not os.access(executable, os.X_OK):
        raise UnsupportedPython
    return Path(executable)


def _environment_key(base: Path, requirements: Path) -> str:
    version = base.stat()
    identity = (_RUNTIME_SCHEMA, str(base.resolve()), platform.machine(), sys.version,
                version.st_dev, version.st_ino, version.st_mtime_ns)
    fingerprint = hashlib.sha256(json.dumps(identity).encode())
    fingerprint.update(requirements.read_bytes())
    return fingerprint.hexdigest()


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Python environment preparation timed out")
    return remaining


@contextmanager
def _lock(path: Path, deadline: float):
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("Invalid Python environment lock")
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(0.1, _remaining(deadline)))
        yield
    finally:
        os.close(descriptor)


def _run(executable: Path, arguments: list[str], deadline: float, *, capture: bool = False) -> str:
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("PYTHON", "PIP_")) and key != "__PYVENV_LAUNCHER__"
    }
    environment["PIP_CONFIG_FILE"] = os.devnull
    _remaining(deadline)
    command = [str(executable), "-I", "-B", *arguments]
    with subprocess.Popen(
        command, env=environment, text=True, start_new_session=True,
        stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
    ) as process:
        try:
            output, _ = process.communicate(timeout=_remaining(deadline))
        except (TimeoutError, subprocess.TimeoutExpired):
            # venv/ensurepip can start their own child processes.
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, command)
    return output or ""


def _certificate(executable: Path, program: str, deadline: float) -> Path:
    location = Path(json.loads(_run(executable, ["-c", program], deadline, capture=True)))
    if not location.is_absolute() or not location.is_file() or executable.parent.parent.resolve() not in location.resolve().parents:
        raise ValueError("Python supplied an invalid certificate bundle")
    return location


def prepare_runtime(root: Path, requirements: Path = _DEPENDENCIES) -> dict[str, str]:
    deadline = time.monotonic() + _TIMEOUT
    base = _base_executable()
    key = _environment_key(base, requirements)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError("Invalid Python environment directory")
    target = root / key
    executable = target / "bin" / "python3"
    marker = target / ".ready"

    with _lock(root / f"{key}.lock", deadline):
        if target.is_symlink():
            raise ValueError("Invalid Python environment directory")
        if marker.is_file():
            try:
                certificates = _certificate(executable, _RUNTIME_CERTIFICATE, deadline)
                return {"pythonPath": str(executable), "certificatesPath": str(certificates)}
            except (FileNotFoundError, subprocess.CalledProcessError, ValueError):
                pass
        if target.exists():
            shutil.rmtree(target)

        # A venv has absolute installation paths. Build it at its final path and
        # publish the readiness marker only after the bridge dependencies import.
        _run(base, ["-m", "venv", str(target)], deadline)
        pip_certificate = _certificate(executable, _PIP_CERTIFICATE, deadline)
        _run(executable, ["-m", "pip", "--isolated", "--no-input", "--disable-pip-version-check",
                          "--timeout", "15", "--retries", "1", "--cert", str(pip_certificate),
                          "install", "--only-binary=:all:", "--no-cache-dir", "--index-url",
                          "https://pypi.org/simple", "-r", str(requirements)], deadline)
        certificates = _certificate(executable, _RUNTIME_CERTIFICATE, deadline)
        marker.touch(exist_ok=False)
        return {"pythonPath": str(executable), "certificatesPath": str(certificates)}


def main(arguments: list[str]) -> int:
    if len(arguments) != 1 or not Path(arguments[0]).is_absolute():
        return 1
    try:
        runtime = prepare_runtime(Path(arguments[0]))
    except UnsupportedPython:
        return 2
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return 1
    print(json.dumps(runtime))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
