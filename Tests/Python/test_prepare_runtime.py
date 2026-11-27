"""Exercise managed Python installation and reuse without contacting PyPI."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Sources/Blinky/Bridge"))
import prepare_runtime as runtime


class RuntimePreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.root = self.folder / "Application Support" / "Blinky"
        self.requirements = self.folder / "requirements.txt"
        self.requirements.write_text("blinkpy==0.25.9\n")
        self.base = Path(sys.executable)
        self.created = []
        self.installed = []
        self.fail_install = False
        base = patch.object(runtime, "_base_executable", return_value=self.base)
        base.start()
        self.addCleanup(base.stop)
        self.commands = patch.object(runtime, "_run", side_effect=self.fake_run)
        self.commands.start()
        self.addCleanup(self.commands.stop)

    def fake_run(self, executable, arguments, deadline, *, capture=False):
        if arguments[:2] == ["-m", "venv"]:
            location = Path(arguments[2])
            self.created.append(location)
            (location / "bin").mkdir(parents=True)
            (location / "bin" / "python3").touch()
            (location / "pip-ca.pem").touch()
            return ""
        location = executable.parent.parent
        if arguments[:2] == ["-m", "pip"]:
            self.installed.append(arguments)
            if self.fail_install:
                raise subprocess.CalledProcessError(1, arguments)
            (location / "certifi-ca.pem").touch()
            return ""
        if arguments == ["-c", runtime._PIP_CERTIFICATE]:
            return json.dumps(str(location / "pip-ca.pem"))
        if arguments == ["-c", runtime._RUNTIME_CERTIFICATE]:
            certificate = location / "certifi-ca.pem"
            if certificate.is_file():
                return json.dumps(str(certificate))
            raise subprocess.CalledProcessError(1, arguments)
        self.fail(f"Unexpected interpreter call: {arguments}")

    def prepare(self):
        return runtime.prepare_runtime(self.root, self.requirements)

    def test_prepares_and_reuses_private_environment(self):
        first = self.prepare()
        self.assertEqual(self.prepare(), first)
        self.assertEqual(len(self.created), 1)
        self.assertEqual(len(self.installed), 1)
        self.assertTrue((self.created[0] / ".ready").is_file())
        self.assertEqual(Path(first["pythonPath"]), self.created[0] / "bin" / "python3")
        self.assertEqual(Path(first["certificatesPath"]), self.created[0] / "certifi-ca.pem")
        for option in ("--isolated", "--no-input", "--only-binary=:all:", "https://pypi.org/simple"):
            self.assertIn(option, self.installed[0])
        self.assertEqual(self.installed[0][-2:], ["-r", str(self.requirements)])

    def test_retries_incomplete_environment(self):
        self.fail_install = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.prepare()
        self.assertFalse((self.created[0] / ".ready").exists())
        self.fail_install = False
        self.prepare()
        self.assertEqual(self.created, [self.created[0]] * 2)
        self.assertTrue((self.created[1] / ".ready").is_file())

    def test_new_requirements_get_a_separate_environment(self):
        first = self.prepare()
        self.requirements.write_text("blinkpy==0.25.10\n")
        second = self.prepare()
        self.assertNotEqual(first["pythonPath"], second["pythonPath"])
        self.assertTrue(Path(first["pythonPath"]).exists())

    def test_concurrent_preparation_installs_once(self):
        entered, release, second_started = Event(), Event(), Event()

        def controlled_run(*args, **kwargs):
            if args[1][:2] == ["-m", "venv"]:
                entered.set()
                self.assertTrue(release.wait(timeout=3))
            return self.fake_run(*args, **kwargs)

        def prepare_second():
            second_started.set()
            return self.prepare()

        with patch.object(runtime, "_run", side_effect=controlled_run), ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(self.prepare)
            try:
                self.assertTrue(entered.wait(timeout=3))
                second = executor.submit(prepare_second)
                self.assertTrue(second_started.wait(timeout=3))
            finally:
                release.set()
            self.assertEqual(first.result(timeout=3), second.result(timeout=3))
        self.assertEqual(len(self.installed), 1)

    def test_unsupported_python_has_distinct_exit_status_and_no_output(self):
        output = io.StringIO()
        with patch.object(runtime, "_base_executable", side_effect=runtime.UnsupportedPython), redirect_stdout(output):
            self.assertEqual(runtime.main([str(self.root)]), 2)
        self.assertEqual(output.getvalue(), "")
        self.assertFalse(self.root.exists())

    def test_entry_point_reports_only_paths(self):
        output = io.StringIO()
        result = {"pythonPath": "/private/venv/bin/python3", "certificatesPath": "/private/venv/ca.pem"}
        with patch.object(runtime, "prepare_runtime", return_value=result), redirect_stdout(output):
            self.assertEqual(runtime.main([str(self.root)]), 0)
        self.assertEqual(json.loads(output.getvalue()), result)


if __name__ == "__main__":
    unittest.main()
