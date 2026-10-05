"""Standalone documentation regressions; fixtures never execute document commands."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from _support import TemporaryScriptTests, cases, load_module

SCRIPT = Path(__file__).resolve().parents[1] / "check_docs.py"
docs = load_module(SCRIPT)


def fence(commands, language="bash", indent=""):
    return "\n".join(indent + line for line in [f"```{language}", *commands.splitlines(), "```", ""])


@cases("check_document", {
    "local_inline_image_and_reference_links": (
        '[guide](nested/guide.md#heading)\n![image](image.png "图")\n[file](<a file(1).md>) [encoded](a%20file(1).md)\n'
        '[root](/README.md#root)\n[anchor](#any-heading) [web](https://example.invalid/missing)\n'
        '[email](mailto:nobody@example.invalid) [cdn](//example.invalid/missing)\n[reference][guide]\n'
        "[guide]: nested/guide.md 'Title'\n`[code](missing.md)`\n<!-- [comment](missing.md) -->\n" +
        fence("value = '[literal](missing.md)'", "python"), [], "hr-demo/docs/guide.md"),
    "missing_links_have_document_and_line": ("# Title\n[missing](no.md)\n![image](no.png)\n[ref]: no.txt\n",
        [f"README.md:{line}: 本地链接目标不存在: {target}" for line, target in ((2, "no.md"), (3, "no.png"), (4, "no.txt"))]),
    "source_scripts_requirements_directories_and_external_clis": (fence("\n".join([
        "python3 -m venv .venv", ".venv/bin/python scripts/check.py",
        ".venv-mcp/bin/python -B scripts/check.py --output-dir missing/output",
        "pip install -r requirements-demo.txt -c requirements-demo.txt",
        ".venv/bin/python -m pip install --requirement=requirements-demo.txt",
        "uv pip install --python .venv/bin/python -rrequirements-demo.txt",
        "uv run --no-sync python scripts/check.py", "ENVIRONMENT=test env MODE=read python scripts/check.py",
        "bash scripts/load_current.sh", "./scripts/load_current.sh", "source scripts/load_current.sh",
        "python3 -m unittest discover -s tests", "python3 -m http.server 8317 \\\n  --directory=hr-demo/wren-project/apps/overview",
        "wren context build", "npx vercel@latest deploy --prod", 'python -c "print(\'scripts/missing.py\')"',
        "python scripts/check.py > missing/generated.txt", ". ./.env.mcp",
    ])), []),
    "missing_script_and_requirements": (fence(
        "python3 scripts/removed.py\nsh scripts/removed.sh\n./scripts/removed.sh\npip install -r missing-requirements.txt", "sh"),
        [f"README.md:{line}: 文件不存在: {target}" for line, target in
         ((2, "scripts/removed.py"), (3, "scripts/removed.sh"), (4, "./scripts/removed.sh"), (5, "missing-requirements.txt"))]),
    "missing_cd_and_directory_and_option_without_value": (
        fence("cd missing\npython3 -m http.server --directory absent") + fence("python -m http.server --directory", "sh"),
        ["README.md:2: 目录不存在: missing", "README.md:3: 工作目录不确定，无法检查 'absent'；请显式 cd", "README.md:6: --directory 缺少路径"]),
    "subshell_restores_cwd_with_inline_and_nested_groups": (
        fence("(cd hr-demo/db && ./load_duckdb.sh)\npython scripts/root.py\n(\n cd hr-demo/db\n (\n  cd tools\n"
              "  python nested.py\n )\n ./load_duckdb.sh # comment with ./missing.sh\n)\npython scripts/root.py") +
        fence("cd hr-demo/db\n./load_duckdb.sh") + fence("python scripts/root.py"), []),
    "plain_cd_persists_only_in_its_fence": (
        fence("cd hr-demo/db\npython check.py\npython ../../scripts/root.py") + fence("python scripts/root.py"), []),
    "semantic_agent_commands_start_in_project_directory": (
        fence("pip install -r ../../requirements-demo.txt\npython ../../scripts/check.py\n../../.venv/bin/wren context validate"),
        [], "hr-demo/wren-project/AGENTS.md"),
    "three_space_indented_command_fences_are_scanned": (
        fence("python scripts/check.py\npython missing.py", indent="   "), ["README.md:3: 文件不存在: missing.py"]),
    "python_heredoc_body_is_never_a_shell_command": (fence(
        "python3 - <<'PY'\ncd missing\npython scripts/not-present.py\nsubprocess.run(['sh', 'load.sh'])\n"
        "value = '[not-a-markdown-link](missing.md)'\nPY\n.venv/bin/python - <<-\"PY\"\n\tcd missing\n\tPY\npython scripts/check.py"), []),
    "heredoc_opening_command_still_checks_directory": (
        fence("python3 - --directory absent <<'PY'\nprint('body')\nPY"), ["README.md:2: 目录不存在: absent"]),
    "historical_range_skips_only_its_commands": ("<!-- docs:historical:start -->\n历史记录\n" + fence("./run_wren.sh") +
        "[old link](missing.md)\n<!-- docs:historical:end -->\n" + fence("python scripts/check.py\npython missing.py"),
        ["README.md:6: 本地链接目标不存在: missing.md", "README.md:10: 文件不存在: missing.py"]),
    "marker_explanations_in_prose_or_fences_do_not_exempt_document": (
        '解释 `<!-- docs:historical -->` 和 `<!-- docs:examples -->` 的含义。\n' +
        fence("<!-- docs:historical -->", "text") + fence("python missing.py"),
        ["README.md:6: 文件不存在: missing.py"]),
    "unmarked_history_heading_and_templates_do_not_exempt_whole_file": ("## 历史复现\n" + fence("python missing.py") +
        fence("python <script.py>\npip install -r '<requirements.txt>'\npython -m http.server --directory '<app directory>'", "sh"),
        ["README.md:3: 文件不存在: missing.py"]),
})
@cases("check_non_source", {
    "existing_non_source_file_and_directory_cannot_satisfy_command": (
        ("ignored/missing.py", "untracked/check.py"),
        "python ignored/missing.py\npython untracked/check.py\npython3 -m http.server --directory ignored\ncd untracked", 4),
    "existing_ignored_database_cannot_be_a_script_or_directory": (
        ("hr-demo/db/duckdb/public.duckdb",), "python hr-demo/db/duckdb/public.duckdb\ncd hr-demo/db/duckdb", 2),
})
@cases("check_references", {
    "deprecated_segment_scan_covers_tracked_text_files": ({
        "hr-demo/wren-project/apps/notes.md": "参见 hr-delivery/validation 的旧题库。\n", "scripts/legacy.py": "OLD = 'hr-delivery/db/duckdb'\n",
    }, [("hr-demo/wren-project/apps/notes.md", 1), ("scripts/legacy.py", 1)]),
    "deprecated_segment_scan_exempts_markers_and_non_text_files": ({
        "hr-demo/docs/old.md": "<!-- docs:historical -->\n历史引用 hr-delivery/。\n" + fence("./run_wren.sh") + "后续 hr-delivery 也不报。\n",
        "hr-demo/docs/mixed.md": "<!-- docs:historical:start -->\n区段内 hr-delivery 不报。\n<!-- docs:historical:end -->\n区段外 hr-delivery 报。\n",
        "scripts/check_docs.py": "RETIRED_SEGMENT = 'hr-delivery'\n", "notes.txt.bin": "hr-delivery",
    }, [("hr-demo/docs/mixed.md", 4)]),
    "deprecated_segment_scan_ignores_markers_in_fences_and_similar_names": ({
        "hr-demo/docs/tricky.md": "解释 `<!-- docs:historical -->` 的含义。\n" + fence("<!-- docs:historical -->", "text") +
                                 "围栏后的整篇标记不豁免：hr-delivery/\n相似名称不报：hr-demo/、ahr-delivery、hr-deliverys。\n",
    }, [("hr-demo/docs/tricky.md", 5)]),
})
class DocumentationTests(TemporaryScriptTests):
    def setUp(self):
        super().setUp()
        for name in ("README.md", "scripts/check.py", "scripts/root.py", "scripts/load_current.sh",
                     "requirements-demo.txt", "tests/test_source.py",
                     "hr-demo/db/check.py", "hr-demo/db/load_duckdb.sh", "hr-demo/db/tools/nested.py",
                     "hr-demo/wren-project/apps/overview/index.html",
                     "hr-demo/docs/nested/guide.md", "hr-demo/docs/image.png", "hr-demo/docs/a file(1).md"):
            self.write(name)

    def check(self, markdown, name="README.md"):
        self.write(name, markdown)
        return docs.check_repository(self.root, self.sources)

    def check_document(self, markdown, expected, name="README.md"):
        self.assertEqual(self.check(markdown, name), expected)

    def check_non_source(self, files, commands, count):
        for name in files:
            self.write(name, source=False)
        errors = self.check(fence(commands))
        self.assertEqual(len(errors), count)
        self.assertTrue(all("不是 Git 源码" in error for error in errors))

    def check_references(self, files, expected):
        for name, text in files.items():
            self.write(name, text)
        self.assertEqual(self.check("# Root\n"), [f"{name}:{line}: 引用已废弃路径段: hr-delivery；仅历史或模板标记可豁免" for name, line in expected])

    def test_examples_and_history_skip_commands_but_check_links(self):
        for marker in ("historical", "examples"):
            with self.subTest(marker=marker):
                markdown = f"# 标题\n<!-- docs:{marker} -->\n说明历史记录或新业务模板。\n"
                self.check_document(markdown + fence("cd hr-delivery/db && ./load.sh\npython missing.py") + "[broken](missing.md)\n",
                                    ["README.md:8: 本地链接目标不存在: missing.md"])

    def test_invalid_or_late_markers_are_errors(self):
        for markdown, line, message in (
            ("<!-- docs:historical:end -->\n", 1, "end 缺少 start"), ("<!-- docs:historical:start -->\n", 1, "start 缺少 end"),
            ("<!-- docs:historical:start -->\n<!-- docs:historical:start -->\n<!-- docs:historical:end -->\n", 2, "不能嵌套"),
            (fence("text", "text") + "<!-- docs:historical -->\n", 4, "必须放在首个代码块前"),
            (fence("text", "text") + "<!-- docs:examples -->\n", 4, "必须放在首个代码块前"),
            ("```bash\npython missing.py\n", 1, "围栏未闭合"),
        ):
            with self.subTest(markdown=markdown):
                self.assertTrue(any(error.startswith(f"README.md:{line}:") and message in error for error in self.check(markdown)))

    def test_unsupported_dynamic_paths_and_malformed_shell_fail_closed(self):
        for command, message in (
            ('cd "$ROOT/hr-demo"', "动态路径"), ('python "$SCRIPT"', "动态路径"),
            ("if true; then python missing.py; fi", "控制流"), ('bash -c "python missing.py"', "不支持 shell -c"),
            ('python "$(choose_script)"', "命令替换"), ("python 'missing.py", "引号"),
            ("( cd missing", "缺少右括号"), ("python - <<'PY'\nprint('unfinished')", "缺少终止符"), ("python missing.py \\", "续行"),
        ):
            with self.subTest(command=command):
                self.assertTrue(any(error.startswith("README.md:2:") and message in error for error in self.check(fence(command))))

    def test_deprecated_paths_and_bare_entry_points_are_active_errors(self):
        errors = self.check(fence("run_wren.sh\nload.sh\npython hr-delivery/validation/v2/run_all.py"))
        for message in ("已废弃入口: run_wren.sh", "已废弃入口: load.sh", "已废弃目录"):
            self.assertTrue(any(message in error for error in errors))

    def test_environment_executables_do_not_require_local_environments(self):
        markdown = fence(".venv/bin/python scripts/check.py\n.venv/bin/wren context build")
        self.check_document(markdown, [])
        for name in (".venv/bin/python", ".venv/bin/wren"):
            self.write(name, source=False)
        self.check_document(markdown, [])

    def test_only_requested_document_scope_is_scanned(self):
        for name in (".agents/skills/README.md", ".claude/skills/README.md", "hr-demo/wren-project/knowledge/sql/query.md",
                     "hr-demo/validation/v2/eval/nested.md", "hr-demo/docs/subdir/guide.md", "hr-demo/validation/v2/README.md"):
            self.write(name, "[broken](missing)")
        self.check_document("# Root\n", [f"{name}:1: 本地链接目标不存在: missing" for name in
                                        ("hr-demo/docs/subdir/guide.md", "hr-demo/validation/v2/README.md")])

    def test_checker_never_executes_document_commands(self):
        self.write("scripts/check.py", "raise RuntimeError('never execute')")
        with mock.patch.object(docs.subprocess, "run", side_effect=AssertionError("no processes")):
            self.check_document(fence("python scripts/check.py"), [])


@cases("check_cli", {
    "cli_accepts_tracked_and_pending_nonignored_source_without_environment": (".venv/bin/python tracked.py\npython pending.py", 0, "检查通过"),
    "cli_rejects_missing_targets_with_nonzero_exit": ("python absent.py", 1, "README.md:2:", "absent.py"),
})
class CommandLineTests(TemporaryScriptTests):
    def setUp(self):
        super().setUp()
        self.assert_exit(self.run_process(["git", "init", "-q", self.root]), 0)
        self.write("README.md", "# Root\n")
        self.write(".gitignore", ".venv/\nignored/\n*.duckdb\n")
        self.write("tracked.py", "raise RuntimeError('never execute')\n")
        self.assert_exit(self.run_process(["git", "-C", self.root, "add", "."]), 0)
        self.write("pending.py", "raise RuntimeError('never execute')\n")

    def run_cli(self, markdown):
        self.write("README.md", markdown)
        return self.run_script(SCRIPT, "--root", self.root, flags=("-I", "-B"))

    def check_cli(self, commands, status, *fragments):
        self.assert_exit(self.run_cli(fence(commands)), status, *fragments, stream="stdout" if status == 0 else "stderr")
        self.assertLessEqual({"tracked.py", "pending.py"}, docs.source_files(self.root))

    def test_cli_rejects_ignored_existing_source_without_changing_git_index(self):
        for name in ("ignored/check.py", ".venv/check.py", "public.duckdb"):
            self.write(name, "raise RuntimeError('never execute')\n", source=False)
        index = self.root / ".git/index"
        before = index.read_bytes()
        result = self.run_cli(fence("python ignored/check.py\npython .venv/check.py\npython public.duckdb\ncd ignored"))
        self.assert_exit(result, 1)
        self.assertEqual(result.stderr.count("不是 Git 源码"), 4)
        self.assertEqual(index.read_bytes(), before)
        self.assertNotIn("ignored/check.py", docs.source_files(self.root))

    def test_cli_requires_git_instead_of_accepting_existing_local_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("# Root\n", encoding="utf-8")
            self.assert_exit(self.run_script(SCRIPT, "--root", root, flags=("-I", "-B")), 1, "Git 源码清单")


if __name__ == "__main__":
    unittest.main()
