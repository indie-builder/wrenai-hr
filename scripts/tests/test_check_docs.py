"""Standalone, standard-library documentation checks; no demo/MCP dependencies."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "check_docs.py"
SPEC = importlib.util.spec_from_file_location("check_docs", SCRIPT)
docs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(docs)


class DocumentationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sources = set()

    def write(self, name, content="", source=True):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if source:
            self.sources.add(name)
        return path

    def check(self, markdown, name="README.md"):
        self.write(name, markdown)
        return docs.check_repository(self.root, self.sources)

    def test_local_inline_image_and_reference_links(self):
        self.write("hr-demo/docs/nested/guide.md")
        self.write("hr-demo/docs/image.png")
        self.write("hr-demo/docs/a file(1).md")
        self.write("README.md")
        markdown = """[guide](nested/guide.md#heading)
![image](image.png "图")
[file](<a file(1).md>) [encoded](a%20file(1).md)
[root](/README.md#root)
[anchor](#any-heading) [web](https://example.invalid/missing)
[email](mailto:nobody@example.invalid) [cdn](//example.invalid/missing)
[reference][guide]
[guide]: nested/guide.md 'Title'
`[code](missing.md)`
<!-- [comment](missing.md) -->
```python
value = '[literal](missing.md)'
```
"""
        self.assertEqual(self.check(markdown, "hr-demo/docs/guide.md"), [])

    def test_missing_links_have_document_and_line(self):
        errors = self.check("# Title\n[missing](no.md)\n![image](no.png)\n[ref]: no.txt\n")
        self.assertEqual(len(errors), 3)
        for number, error in enumerate(errors, 2):
            self.assertTrue(error.startswith(f"README.md:{number}:"), error)
            self.assertIn("链接目标不存在", error)

    def test_source_scripts_requirements_directories_and_external_clis(self):
        self.write("scripts/check.py")
        self.write("scripts/load_current.sh")
        self.write("requirements-demo.txt")
        self.write("tests/test_source.py")
        self.write("hr-demo/wren-project/apps/overview/index.html")
        markdown = """```bash
python3 -m venv .venv
.venv/bin/python scripts/check.py
.venv-mcp/bin/python -B scripts/check.py --output-dir missing/output
pip install -r requirements-demo.txt -c requirements-demo.txt
.venv/bin/python -m pip install --requirement=requirements-demo.txt
uv pip install --python .venv/bin/python -rrequirements-demo.txt
uv run --no-sync python scripts/check.py
ENVIRONMENT=test env MODE=read python scripts/check.py
bash scripts/load_current.sh
./scripts/load_current.sh
source scripts/load_current.sh
python3 -m unittest discover -s tests
python3 -m http.server 8317 \\
  --directory=hr-demo/wren-project/apps/overview
wren context build
npx vercel@latest deploy --prod
python -c "print('scripts/missing.py')"
python scripts/check.py > missing/generated.txt
. ./.env.mcp
```
"""
        self.assertEqual(self.check(markdown), [])

    def test_missing_script_and_requirements(self):
        errors = self.check("""```sh
python3 scripts/removed.py
sh scripts/removed.sh
./scripts/removed.sh
pip install -r missing-requirements.txt
```
""")
        self.assertEqual(len(errors), 4)
        self.assertIn("README.md:2: 文件不存在: scripts/removed.py", errors)
        self.assertTrue(any("missing-requirements.txt" in error for error in errors))

    def test_missing_cd_and_directory_and_option_without_value(self):
        errors = self.check("""```bash
cd missing
python3 -m http.server --directory absent
```
```sh
python -m http.server --directory
```
""")
        self.assertEqual(len(errors), 3)
        self.assertIn("README.md:2: 目录不存在: missing", errors)
        self.assertIn("工作目录不确定", errors[1])
        self.assertIn("--directory 缺少路径", errors[2])

    def test_subshell_restores_cwd_with_inline_and_nested_groups(self):
        self.write("scripts/root.py")
        self.write("hr-demo/db/load_duckdb.sh")
        self.write("hr-demo/db/tools/nested.py")
        markdown = """```bash
(cd hr-demo/db && ./load_duckdb.sh)
python scripts/root.py
(
  cd hr-demo/db
  (
    cd tools
    python nested.py
  )
  ./load_duckdb.sh # comment with ./missing.sh
)
python scripts/root.py
```
```bash
cd hr-demo/db
./load_duckdb.sh
```
```bash
python scripts/root.py
```
"""
        self.assertEqual(self.check(markdown), [])

    def test_plain_cd_persists_only_in_its_fence(self):
        self.write("hr-demo/db/check.py")
        self.write("scripts/root.py")
        errors = self.check("""```bash
cd hr-demo/db
python check.py
python ../../scripts/root.py
```
```bash
python scripts/root.py
```
""")
        self.assertEqual(errors, [])

    def test_semantic_agent_commands_start_in_project_directory(self):
        self.write("requirements-demo.txt")
        self.write("scripts/check.py")
        self.assertEqual(self.check("""```bash
pip install -r ../../requirements-demo.txt
python ../../scripts/check.py
../../.venv/bin/wren context validate
```
""", "hr-demo/wren-project/AGENTS.md"), [])

    def test_three_space_indented_command_fences_are_scanned(self):
        self.write("scripts/check.py")
        self.assertEqual(self.check("   ```bash\n   python scripts/check.py\n   python missing.py\n   ```\n"),
                         ["README.md:3: 文件不存在: missing.py"])

    def test_python_heredoc_body_is_never_a_shell_command(self):
        self.write("scripts/check.py")
        self.assertEqual(self.check("""```bash
python3 - <<'PY'
cd missing
python scripts/not-present.py
subprocess.run(['sh', 'load.sh'])
value = '[not-a-markdown-link](missing.md)'
PY
.venv/bin/python - <<-"PY"
\tcd missing
\tPY
python scripts/check.py
```
"""), [])

    def test_heredoc_opening_command_still_checks_directory(self):
        errors = self.check("""```bash
python3 - --directory absent <<'PY'
print('body')
PY
```
""")
        self.assertEqual(errors, ["README.md:2: 目录不存在: absent"])

    def test_examples_and_history_skip_commands_but_check_links(self):
        for marker in ("historical", "examples"):
            with self.subTest(marker=marker):
                self.assertEqual(self.check(f"""# 标题
<!-- docs:{marker} -->
说明这些是历史记录或新业务模板。
```bash
cd hr-delivery/db && ./load.sh
python missing.py
```
[broken](missing.md)
"""), ["README.md:8: 本地链接目标不存在: missing.md"])

    def test_historical_range_skips_only_its_commands(self):
        self.write("scripts/check.py")
        errors = self.check("""<!-- docs:historical:start -->
历史记录
```bash
./run_wren.sh
```
[old link](missing.md)
<!-- docs:historical:end -->
```bash
python scripts/check.py
python missing.py
```
""")
        self.assertEqual(errors, ["README.md:6: 本地链接目标不存在: missing.md", "README.md:10: 文件不存在: missing.py"])

    def test_marker_explanations_in_prose_or_fences_do_not_exempt_document(self):
        errors = self.check("""解释 `<!-- docs:historical -->` 和 `<!-- docs:examples -->` 的含义。
```text
<!-- docs:historical -->
```
```bash
python missing.py
```
""")
        self.assertEqual(errors, ["README.md:6: 文件不存在: missing.py"])

    def test_unmarked_history_heading_and_templates_do_not_exempt_whole_file(self):
        errors = self.check("""## 历史复现
```bash
python missing.py
```
```sh
python <script.py>
pip install -r '<requirements.txt>'
python -m http.server --directory '<app directory>'
```
""")
        self.assertEqual(errors, ["README.md:3: 文件不存在: missing.py"])

    def test_invalid_or_late_markers_are_errors(self):
        for markdown, expected in (
            ("<!-- docs:historical:end -->\n", "end 缺少 start"),
            ("<!-- docs:historical:start -->\n", "start 缺少 end"),
            ("<!-- docs:historical:start -->\n<!-- docs:historical:start -->\n<!-- docs:historical:end -->\n", "不能嵌套"),
            ("```text\ntext\n```\n<!-- docs:historical -->\n", "必须放在首个代码块前"),
        ):
            with self.subTest(markdown=markdown):
                self.assertTrue(any(expected in error for error in self.check(markdown)))

    def test_deprecated_paths_and_bare_entry_points_are_active_errors(self):
        errors = self.check("""```bash
run_wren.sh
load.sh
python hr-delivery/validation/v2/run_all.py
```
""")
        self.assertTrue(any("已废弃入口: run_wren.sh" in error for error in errors))
        self.assertTrue(any("已废弃入口: load.sh" in error for error in errors))
        self.assertTrue(any("已废弃目录" in error for error in errors))

    def test_deprecated_segment_scan_covers_tracked_text_files(self):
        self.write("hr-demo/wren-project/apps/notes.md", "参见 hr-delivery/validation 的旧题库。\n")
        self.write("scripts/legacy.py", "OLD = 'hr-delivery/db/duckdb'\n")
        errors = self.check("# Root\n")
        self.assertEqual(sorted(errors), [
            "hr-demo/wren-project/apps/notes.md:1: 引用已废弃路径段: hr-delivery；仅历史或模板标记可豁免",
            "scripts/legacy.py:1: 引用已废弃路径段: hr-delivery；仅历史或模板标记可豁免",
        ])

    def test_deprecated_segment_scan_exempts_markers_and_non_text_files(self):
        self.write("hr-demo/docs/old.md",
                   "<!-- docs:historical -->\n历史记录引用 hr-delivery/。\n```bash\n./run_wren.sh\n```\n后续 hr-delivery 也不报。\n")
        self.write("hr-demo/docs/mixed.md",
                   "<!-- docs:historical:start -->\n区段内 hr-delivery 不报。\n<!-- docs:historical:end -->\n区段外 hr-delivery 报。\n")
        self.write("scripts/check_docs.py", "RETIRED_SEGMENT = 'hr-delivery'\n")
        self.write("notes.txt.bin", "hr-delivery")
        errors = self.check("# Root\n")
        self.assertEqual(errors, ["hr-demo/docs/mixed.md:4: 引用已废弃路径段: hr-delivery；仅历史或模板标记可豁免"])

    def test_deprecated_segment_scan_ignores_markers_in_fences_and_similar_names(self):
        self.write("hr-demo/docs/tricky.md", "解释 `<!-- docs:historical -->` 的含义。\n"
                   "```text\n<!-- docs:historical -->\n```\n"
                   "围栏后的整篇标记不豁免：hr-delivery/\n"
                   "相似名称不报：hr-demo/、ahr-delivery、hr-deliverys。\n")
        errors = self.check("# Root\n")
        self.assertEqual(errors, ["hr-demo/docs/tricky.md:5: 引用已废弃路径段: hr-delivery；仅历史或模板标记可豁免"])

    def test_environment_executables_do_not_require_local_environments(self):
        self.write("scripts/check.py")
        markdown = "```bash\n.venv/bin/python scripts/check.py\n.venv/bin/wren context build\n```\n"
        self.assertEqual(self.check(markdown), [])
        self.write(".venv/bin/python", source=False)
        self.write(".venv/bin/wren", source=False)
        self.assertEqual(self.check(markdown), [])

    def test_existing_non_source_file_and_directory_cannot_satisfy_command(self):
        self.write("ignored/missing.py", source=False)
        self.write("untracked/check.py", source=False)
        errors = self.check("""```bash
python ignored/missing.py
python untracked/check.py
python3 -m http.server --directory ignored
cd untracked
```
""")
        self.assertEqual(len(errors), 4)
        self.assertTrue(all("不是 Git 源码" in error for error in errors))

    def test_existing_ignored_database_cannot_be_a_script_or_directory(self):
        self.write("hr-demo/db/duckdb/public.duckdb", source=False)
        errors = self.check("""```bash
python hr-demo/db/duckdb/public.duckdb
cd hr-demo/db/duckdb
```
""")
        self.assertEqual(len(errors), 2)
        self.assertTrue(all("不是 Git 源码" in error for error in errors))

    def test_only_requested_document_scope_is_scanned(self):
        self.write(".agents/skills/README.md", "[broken](missing)")
        self.write(".claude/skills/README.md", "[broken](missing)")
        self.write("hr-demo/wren-project/knowledge/sql/query.md", "[broken](missing)")
        self.write("hr-demo/validation/v2/eval/nested.md", "[broken](missing)")
        self.write("hr-demo/docs/subdir/guide.md", "[broken](missing)")
        self.write("hr-demo/validation/v2/README.md", "[broken](missing)")
        errors = self.check("# Root\n")
        self.assertEqual(len(errors), 2)
        self.assertTrue(any("hr-demo/docs/subdir/guide.md:1:" in error for error in errors))
        self.assertTrue(any("hr-demo/validation/v2/README.md:1:" in error for error in errors))

    def test_unsupported_dynamic_paths_and_malformed_shell_fail_closed(self):
        for command, message in (
            ('cd "$ROOT/hr-demo"', "动态路径"),
            ('python "$SCRIPT"', "动态路径"),
            ('if true; then python missing.py; fi', "控制流"),
            ('bash -c "python missing.py"', "不支持 shell -c"),
            ('python "$(choose_script)"', "命令替换"),
            ("python 'missing.py", "引号"),
            ("( cd missing", "缺少右括号"),
            ("python - <<'PY'\nprint('unfinished')", "缺少终止符"),
            ("python missing.py \\", "续行"),
        ):
            with self.subTest(command=command):
                self.assertTrue(any(message in error for error in self.check(f"```bash\n{command}\n```\n")))

    def test_checker_never_executes_document_commands(self):
        self.write("scripts/check.py", "raise RuntimeError('never execute')")
        with mock.patch.object(docs.subprocess, "run", side_effect=AssertionError("no processes")):
            self.assertEqual(self.check("```bash\npython scripts/check.py\n```\n"), [])


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, capture_output=True)
        (self.root / "README.md").write_text("# Root\n", encoding="utf-8")
        (self.root / ".gitignore").write_text(".venv/\nignored/\n*.duckdb\n", encoding="utf-8")
        (self.root / "tracked.py").write_text("raise RuntimeError('never execute')\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True, capture_output=True)
        (self.root / "pending.py").write_text("raise RuntimeError('never execute')\n", encoding="utf-8")

    def run_cli(self, markdown):
        (self.root / "README.md").write_text(markdown, encoding="utf-8")
        return subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "--root", str(self.root)],
                              cwd=self.temporary.name, capture_output=True, text=True, timeout=10)

    def test_cli_accepts_tracked_and_pending_nonignored_source_without_environment(self):
        result = self.run_cli("```bash\n.venv/bin/python tracked.py\npython pending.py\n```\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("检查通过", result.stdout)
        inventory = docs.source_files(self.root)
        self.assertIn("tracked.py", inventory)
        self.assertIn("pending.py", inventory)

    def test_cli_rejects_missing_targets_with_nonzero_exit(self):
        result = self.run_cli("```bash\npython absent.py\n```\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("README.md:2:", result.stderr)
        self.assertIn("absent.py", result.stderr)

    def test_cli_rejects_ignored_existing_source_without_changing_git_index(self):
        (self.root / "ignored").mkdir()
        (self.root / "ignored/check.py").write_text("raise RuntimeError('never execute')\n", encoding="utf-8")
        (self.root / ".venv").mkdir()
        (self.root / ".venv/check.py").write_text("raise RuntimeError('never execute')\n", encoding="utf-8")
        (self.root / "public.duckdb").write_text("fake ignored database", encoding="utf-8")
        index_before = (self.root / ".git/index").read_bytes()
        result = self.run_cli("```bash\npython ignored/check.py\npython .venv/check.py\npython public.duckdb\ncd ignored\n```\n")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.count("不是 Git 源码"), 4)
        self.assertEqual((self.root / ".git/index").read_bytes(), index_before)
        self.assertNotIn("ignored/check.py", docs.source_files(self.root))

    def test_cli_requires_git_instead_of_accepting_existing_local_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("# Root\n", encoding="utf-8")
            result = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "--root", str(root)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Git 源码清单", result.stderr)


if __name__ == "__main__":
    unittest.main()
