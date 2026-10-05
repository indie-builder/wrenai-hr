"""Standard-library fixtures shared by documentation and skill CLI tests."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


def load_module(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cases(method, rows):
    """Expand named data rows into independently reported unittest methods."""
    def decorate(cls):
        for name, args in rows.items():
            def test(self, args=args):
                getattr(self, method)(*args)
            setattr(cls, f"test_{name}", test)
        return cls
    return decorate


class TemporaryScriptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="script-tests-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sources = set()
        # Runner GT subprocesses also inherit this, avoiding bytecode writes.
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

    def write(self, name, content="", source=True):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if source:
            self.sources.add(name)
        return path

    def run_process(self, command):
        try:
            return subprocess.run(list(map(str, command)), cwd=self.root, env=self.env,
                                  capture_output=True, text=True, encoding="utf-8", timeout=8)
        except subprocess.TimeoutExpired as error:
            self.fail(f"CLI 超过 8 秒: {command!r}; stdout={error.stdout!r}; stderr={error.stderr!r}")

    def run_script(self, script, *args, flags=("-B",)):
        return self.run_process([sys.executable, *flags, script, *args])

    def assert_exit(self, result, expected, *fragments, stream="stderr"):
        context = f"命令={result.args!r}; rc={result.returncode}; stdout={result.stdout!r}; stderr={result.stderr!r}"
        self.assertEqual(result.returncode, expected, context)
        for fragment in fragments:
            self.assertIn(fragment, getattr(result, stream), context)
