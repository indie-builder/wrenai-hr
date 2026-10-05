#!/usr/bin/env python3
"""Read-only, standard-library checks for operational Markdown and retired paths.

Only Git's source inventory is executed; documents and network are never run.
Targets must exist in tracked or pending non-ignored sources, excluding local
ignored databases/environments. Local inline/image/reference links are checked
outside fences and comments, including history/templates (anchors are ignored).

Standalone docs:historical/examples header comments exempt subsequent commands;
docs:historical:start/end delimit balanced ranges. The shared scanner validates
markers/fences and skips fenced/history references in all source text files.
Shell checks cover literal entry points, pip requirements, cd and directory
options, with per-fence cwd, subshells, continuations and heredocs. Environment
executables, generated outputs, Python -c and placeholders are exempt; dynamic
paths/control flow require explicit rewriting. This is not a shell interpreter.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

# -I removes the script directory; restore only this trusted sibling directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _docs_scan import CodeBlock, RETIRED_SEGMENT, SEGMENT, scan
from _docs_shell import ShellChecks, PLACEHOLDER

DOCUMENTS = (
    "README.md", "AGENTS.md", "hr-demo/README.md",
    "hr-demo/wren-project/AGENTS.md", "hr-demo/GOAL.md",
    "hr-demo/docs/**/*.md", "hr-demo/validation/*.md", "hr-demo/validation/v2/*.md",
)
INLINE_LINK = re.compile(
    r"\[[^\]\n]*\]\(\s*(<[^>\n]+>|(?:\\.|[^\\\s()])+(?:\((?:\\.|[^\\()\n])*\)(?:\\.|[^\\\s()])*)*)"
    r"\s*(?:\"[^\"]*\"|'[^']*'|\([^)]*\))?\s*\)"
)
REFERENCE = re.compile(r"^ {0,3}\[[^\]]+\]:\s*(<[^>\n]+>|\S+)")
TEXT_SUFFIXES = frozenset({".md", ".py", ".yml", ".yaml", ".json", ".toml", ".txt", ".sh", ".html", ".sql", ".cfg", ".example"})
CHECKER_FIXTURES = frozenset({"scripts/check_docs.py", "scripts/_docs_scan.py", "scripts/tests/test_check_docs.py"})


def source_files(root: Path) -> set[str]:
    """Read tracked and pending sources without changing Git's index/worktree."""
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True, timeout=10,
    )
    if result.returncode:
        raise ValueError("无法读取 Git 源码清单；--root 必须指向仓库根目录")
    return {os.fsdecode(name) for name in result.stdout.split(b"\0") if name}


def document_paths(root: Path) -> list[Path]:
    return sorted({path for pattern in DOCUMENTS for path in root.glob(pattern) if path.is_file()})


class Checker(ShellChecks):
    def __init__(self, root: Path, sources: set[str]):
        self.root = root.resolve()
        self.sources = sources
        self.directories = {"."} | {parent.as_posix() for name in sources for parent in Path(name).parents}
        self.errors: list[str] = []

    def error(self, line: int, message: str) -> None:
        self.errors.append(f"{self.path.relative_to(self.root).as_posix()}:{line}: {message}")

    def target(self, line: int, cwd: Path | None, value: str, directory: bool = False) -> Path | None:
        if PLACEHOLDER.search(value):
            return None
        if any(character in value for character in "$`*?[]~"):
            self.error(line, f"无法静态检查动态路径 {value!r}；请改为明确路径或标明模板")
            return None
        if cwd is None and not Path(value).is_absolute():
            self.error(line, f"工作目录不确定，无法检查 {value!r}；请显式 cd")
            return None
        path = Path(os.path.abspath((cwd or self.root) / value))
        try:
            name = path.relative_to(self.root).as_posix()
            path.resolve().relative_to(self.root)
        except ValueError:
            self.error(line, f"源码目标越出仓库: {value}")
            return None
        exists = path.is_dir() if directory else path.is_file()
        allowed = name in (self.directories if directory else self.sources)
        if not exists:
            self.error(line, f"{'目录' if directory else '文件'}不存在: {value}")
        elif not allowed:
            self.error(line, f"目标不是 Git 源码（可能被忽略）: {value}")
        else:
            return path
        return None

    def links(self, line: int, text: str) -> None:
        text = re.sub(r"(`+).*?\1", "", text)
        targets = [match.group(1) for match in INLINE_LINK.finditer(text)]
        reference = REFERENCE.match(text)
        if reference:
            targets.append(reference.group(1))
        for value in targets:
            value = value.removeprefix("<").removesuffix(">")
            value = re.sub(r"\\([\\ ()])", r"\1", value)
            parsed = urlsplit(value)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            path = (self.root if parsed.path.startswith("/") else self.path.parent) / unquote(parsed.path).lstrip("/")
            try:
                path.resolve().relative_to(self.root)
            except ValueError:
                self.error(line, f"本地链接越出仓库: {value}")
                continue
            if not path.exists():
                self.error(line, f"本地链接目标不存在: {value}")

    def document(self, path: Path) -> None:
        self.path = path
        text = path.read_text(encoding="utf-8")
        # Mask comments without moving link diagnostics to different lines.
        visible = re.sub(r"<!--.*?-->", lambda match: re.sub(r"[^\n]", " ", match.group()), text, flags=re.S).splitlines()
        for item in scan(text, self.error):
            if isinstance(item, CodeBlock):
                if item.active and item.language in {"bash", "sh", "shell"}:
                    self.shell(item.lines)
            else:
                self.links(item.number, visible[item.number - 1])


def deprecated_segments(root: Path, sources: set[str]) -> list[str]:
    """Report retired path segments outside fences and explicit history/templates."""
    errors = []
    for name in sorted(sources):
        if name in CHECKER_FIXTURES or Path(name).suffix not in TEXT_SUFFIXES:
            continue
        try:
            text = (root / name).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for item in scan(text):
            if not isinstance(item, CodeBlock) and item.active and RETIRED_SEGMENT in SEGMENT.split(item.text):
                errors.append(f"{name}:{item.number}: 引用已废弃路径段: {RETIRED_SEGMENT}；仅历史或模板标记可豁免")
    return errors


def check_repository(root: Path, sources: set[str] | None = None) -> list[str]:
    root = root.resolve()
    checker = Checker(root, source_files(root) if sources is None else sources)
    paths = document_paths(root)
    if not paths:
        return ["README.md:1: 未找到范围内文档"]
    for path in paths:
        try:
            checker.document(path)
        except (OSError, UnicodeError, ValueError) as error:
            checker.error(1, f"无法检查文档: {error}")
    checker.errors.extend(deprecated_segments(root, checker.sources))
    return checker.errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="仓库根目录")
    args = parser.parse_args(argv)
    try:
        errors = check_repository(args.root)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        print(f"README.md:1: {error}", file=sys.stderr)
        return 1
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    print(f"文档检查通过（{len(document_paths(args.root))} 个文档）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
