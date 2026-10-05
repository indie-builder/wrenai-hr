#!/usr/bin/env python3
"""Read-only, standard-library checks for this repository's operational Markdown.

Run ``python3 scripts/check_docs.py``; only ``git ls-files`` is invoked, never
commands from documents or network requests. Source targets must exist and be
listed by Git (--cached --others --exclude-standard): tracked files and pending,
non-ignored additions count, local ignored databases/environments do not.

Standalone comments outside code fences control command scanning:
* <!-- docs:historical --> in the header marks the whole document as history.
* <!-- docs:historical:start --> / <!-- docs:historical:end --> delimit history.
* <!-- docs:examples --> in the header marks new-business command templates.
These explicit exceptions skip commands only; local links are always checked.
Header markers must precede the first code fence; ranges must be balanced.

Scope is deliberately small: inline/image and reference-definition Markdown
links, and bash/sh/shell fences with literal Python/shell script entry points,
pip requirements, cd, --directory and unittest discovery directories. Each
fence starts at the repository root, except wren-project/AGENTS.md which starts
at that project. Simple subshells, separators, comments, continuations and
heredocs are supported. Heredoc bodies, -c Python code, generated output flags,
external CLIs and environment executables are not inspected. Angle-bracket
placeholders are exempt. Dynamic paths, shell control flow and substitutions
require manual rewriting (or an explicitly justified examples marker); this is
not a shell interpreter, heading validator or complete Markdown parser.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from urllib.parse import unquote, urlsplit


DOCUMENTS = (
    "README.md", "AGENTS.md", "hr-demo/README.md",
    "hr-demo/wren-project/AGENTS.md", "hr-demo/GOAL.md",
)
GLOBS = ("hr-demo/docs/**/*.md", "hr-demo/validation/*.md", "hr-demo/validation/v2/*.md")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
MARKER = re.compile(r"^\s*<!-- docs:(historical(?::start|:end)?|examples) -->\s*$")
INLINE_LINK = re.compile(
    r"\[[^\]\n]*\]\(\s*(<[^>\n]+>|(?:\\.|[^\\\s()])+(?:\((?:\\.|[^\\()\n])*\)(?:\\.|[^\\\s()])*)*)"
    r"\s*(?:\"[^\"]*\"|'[^']*'|\([^)]*\))?\s*\)"
)
REFERENCE = re.compile(r"^ {0,3}\[[^\]]+\]:\s*(<[^>\n]+>|\S+)")
HEREDOC = re.compile(r"<<(-?)\s*(?:'([\w-]+)'|\"([\w-]+)\"|([\w-]+))(?=$|\s|[;)&|<>])")
ENV_EXECUTABLE = re.compile(r"(?:^|/)\.venv(?:-[\w-]+)?/bin/[\w.-]+$")
PYTHON = re.compile(r"python(?:\d+(?:\.\d+)*)?$")
RETIRED = {"run_wren.sh", "load.sh"}
CONTROL_FLOW = {"if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done", "case", "esac", "function", "{", "}"}


def source_files(root: Path) -> set[str]:
    """Read Git's source inventory without changing the index or worktree."""
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True, timeout=10,
    )
    if result.returncode:
        raise ValueError("无法读取 Git 源码清单；--root 必须指向仓库根目录")
    return {os.fsdecode(name) for name in result.stdout.split(b"\0") if name}


def document_paths(root: Path) -> list[Path]:
    paths = {root / name for name in DOCUMENTS if (root / name).is_file()}
    for pattern in GLOBS:
        paths.update(path for path in root.glob(pattern) if path.is_file())
    return sorted(paths)


def placeholder(value: str) -> bool:
    return bool(re.search(r"<[^>]+>", value))


class Checker:
    def __init__(self, root: Path, sources: set[str]):
        self.root = root.resolve()
        self.sources = sources
        self.directories = {"."}
        for name in sources:
            self.directories.update(parent.as_posix() for parent in Path(name).parents)
        self.errors: list[str] = []

    def error(self, document: Path, line: int, message: str) -> None:
        self.errors.append(f"{document.relative_to(self.root).as_posix()}:{line}: {message}")

    def target(self, document: Path, line: int, cwd: Path | None, value: str,
               directory: bool = False) -> Path | None:
        if placeholder(value):
            return None
        if any(character in value for character in "$`*?[]~"):
            self.error(document, line, f"无法静态检查动态路径 {value!r}；请改为明确路径或标明模板")
            return None
        if cwd is None and not Path(value).is_absolute():
            self.error(document, line, f"工作目录不确定，无法检查 {value!r}；请显式 cd")
            return None
        path = Path(os.path.abspath((cwd or self.root) / value))
        try:
            name = path.relative_to(self.root).as_posix()
            path.resolve().relative_to(self.root)
        except ValueError:
            self.error(document, line, f"源码目标越出仓库: {value}")
            return None
        exists = path.is_dir() if directory else path.is_file()
        allowed = name in (self.directories if directory else self.sources)
        if not exists:
            self.error(document, line, f"{'目录' if directory else '文件'}不存在: {value}")
        elif not allowed:
            self.error(document, line, f"目标不是 Git 源码（可能被忽略）: {value}")
        else:
            return path
        return None

    def links(self, document: Path, line: int, text: str) -> None:
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
            path = (self.root if parsed.path.startswith("/") else document.parent) / unquote(parsed.path).lstrip("/")
            try:
                path.resolve().relative_to(self.root)
            except ValueError:
                self.error(document, line, f"本地链接越出仓库: {value}")
                continue
            if not path.exists():
                self.error(document, line, f"本地链接目标不存在: {value}")

    def command(self, document: Path, line: int, tokens: list[str], cwd: Path | None) -> Path | None:
        # Output redirections are not source dependencies; input redirections are
        # outside this checker's entry-point scope. Never inspect command output.
        args: list[str] = []
        skip = False
        for token in tokens:
            if skip:
                skip = False
            elif token in {">", ">>", "<", "2>", "2>>", "2>&1"}:
                skip = token != "2>&1"
            elif re.match(r"^(?:\d*>>?|<)[^<>]", token) and not placeholder(token):
                continue
            else:
                args.append(token)
        while args and re.match(r"^[A-Za-z_]\w*=", args[0]):
            args.pop(0)
        if not args:
            return cwd
        # Small wrappers used by Python tooling; no guessing arbitrary CLI args.
        if args[0] in {"env", "command", "exec"}:
            args = args[1:]
            while args and re.match(r"^[A-Za-z_]\w*=", args[0]):
                args.pop(0)
        if args[:2] == ["uv", "run"]:
            args = args[2:]
            while args and args[0] in {"--no-sync", "--locked", "--frozen", "--"}:
                args.pop(0)
        if not args:
            return cwd
        executable = args[0]
        program = Path(executable).name
        if program in CONTROL_FLOW or any("$(" in arg or "`" in arg for arg in args):
            self.error(document, line, "不支持 shell 控制流/命令替换；请拆为明确命令")
            return None
        for token in args:
            value = token.partition("=")[2] if token.startswith("--") and "=" in token else token
            if not placeholder(value) and "hr-delivery" in value.split("/"):
                self.error(document, line, f"活跃命令使用已废弃目录: {value}")
        if program in RETIRED:
            self.error(document, line, f"活跃命令使用已废弃入口: {executable}")
        elif not ENV_EXECUTABLE.search(executable) and ("/" in executable or executable.endswith((".py", ".sh"))):
            self.target(document, line, cwd, executable)
        if program == "cd":
            values = [value for value in args[1:] if value != "--"]
            if len(values) != 1 or values[0] == "-":
                self.error(document, line, "cd 需要一个明确的仓库目录")
                return None
            return self.target(document, line, cwd, values[0], directory=True)
        if program in {"source", "."} and len(args) > 1 and args[1].endswith((".sh", ".py")):
            self.target(document, line, cwd, args[1])
        if PYTHON.fullmatch(program) or program in {"bash", "sh"}:
            position = 1
            while position < len(args):
                value = args[position]
                if value in {"-m", "-c"}:
                    if value == "-c" and program in {"bash", "sh"}:
                        self.error(document, line, "不支持 shell -c；请展开为明确命令")
                    break
                if value in {"-W", "-X"}:
                    position += 2
                    continue
                if value == "-":
                    break
                if value == "--":
                    position += 1
                    if position == len(args):
                        break
                    value = args[position]
                elif value.startswith("-"):
                    position += 1
                    continue
                self.target(document, line, cwd, value)
                break
        pip = program.startswith("pip") or args[:2] == ["uv", "pip"] or (PYTHON.fullmatch(program) and args[1:3] == ["-m", "pip"])
        discovery = PYTHON.fullmatch(program) and args[1:4] == ["-m", "unittest", "discover"]
        options = {"--directory": True}
        if pip:
            options.update({"-r": False, "--requirement": False, "-c": False, "--constraint": False})
        if discovery:
            options.update({"-s": True, "--start-directory": True, "-t": True, "--top-level-directory": True})
        for position, token in enumerate(args[1:], 1):
            option, equals, value = token.partition("=")
            if pip and token.startswith(("-r", "-c")) and len(token) > 2 and not token.startswith("--"):
                option, equals, value = token[:2], "=", token[2:]
            if option not in options:
                continue
            if not equals:
                value = args[position + 1] if position + 1 < len(args) else ""
            if not value or value.startswith("--"):
                self.error(document, line, f"{option} 缺少路径")
            else:
                self.target(document, line, cwd, value, directory=options[option])
        return cwd

    def shell(self, document: Path, lines: list[tuple[int, str]]) -> None:
        cwd: Path | None = document.parent if document.relative_to(self.root).as_posix() == "hr-demo/wren-project/AGENTS.md" else self.root
        stack: list[Path | None] = []
        pending = ""
        start = 0
        heredoc: tuple[str, bool, int] | None = None
        for number, text in lines:
            if heredoc:
                delimiter, tabs, _ = heredoc
                if (text.lstrip("\t") if tabs else text) == delimiter:
                    heredoc = None
                continue
            if not pending:
                start = number
            if text.rstrip().endswith("\\"):
                pending += text.rstrip()[:-1] + " "
                continue
            logical = pending + text
            pending = ""
            if logical.lstrip().startswith("$ "):
                logical = logical.lstrip()[2:]
            # shlex recognizes quotes/comments; ignore apparent heredoc syntax
            # in comments or quoted strings by looking for a real << token.
            try:
                probe = shlex.split(logical, comments=True)
            except ValueError as error:
                self.error(document, start, f"无法解析 shell 引号: {error}")
                continue
            if any("$(" in token or "`" in token for token in probe):
                self.error(document, start, "不支持 shell 命令替换；请拆为明确命令")
                continue
            if any(token.startswith("<<") for token in probe):
                matches = list(HEREDOC.finditer(logical))
                if len(matches) != 1:
                    self.error(document, start, "仅支持单个、字面终止符的 heredoc")
                    continue
                match = matches[0]
                heredoc = (next(group for group in match.groups()[1:] if group), bool(match.group(1)), start)
                logical = logical[:match.start()] + logical[match.end():]
            try:
                lexer = shlex.shlex(logical, posix=True, punctuation_chars="();&|")
                lexer.whitespace_split = True
                tokens = list(lexer)
            except ValueError as error:
                self.error(document, start, f"无法解析 shell: {error}")
                continue
            normalized = []
            for token in tokens:
                normalized.extend(re.findall(r"&&|\|\||[();&|]", token) if re.fullmatch(r"[();&|]+", token) else [token])
            command: list[str] = []
            for token in normalized + [";"]:
                if token in {"(", ")", ";", "&&", "||", "|", "&"}:
                    if command:
                        cwd = self.command(document, start, command, cwd)
                        command = []
                    if token == "(":
                        stack.append(cwd)
                    elif token == ")":
                        if stack:
                            cwd = stack.pop()
                        else:
                            self.error(document, start, "子 shell 右括号没有对应左括号")
                else:
                    command.append(token)
        if pending:
            self.error(document, start, "命令续行缺少后续行")
        if heredoc:
            self.error(document, heredoc[2], f"heredoc 缺少终止符 {heredoc[0]}")
        if stack:
            self.error(document, lines[-1][0], "子 shell 缺少右括号")

    def document(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        # Mask comments for links without changing their line numbers.
        visible = re.sub(r"<!--.*?-->", lambda match: re.sub(r"[^\n]", " ", match.group()), text, flags=re.S).splitlines()
        fence: tuple[str, int, str, int] | None = None
        block: list[tuple[int, str]] = []
        exempt = False
        history_start: int | None = None
        seen_fence = False
        for number, line in enumerate(text.splitlines(), 1):
            match = FENCE.match(line)
            if fence:
                if match and match[1][0] == fence[0] and len(match[1]) >= fence[1] and not match[2].strip():
                    if fence[2] in {"bash", "sh", "shell"} and not exempt and history_start is None:
                        self.shell(path, block)
                    fence = None
                    block = []
                else:
                    block.append((number, line))
                continue
            marker = MARKER.match(line)
            if marker:
                kind = marker[1]
                if kind in {"historical", "examples"}:
                    if seen_fence:
                        self.error(path, number, "整篇历史/模板标记必须放在首个代码块前")
                    else:
                        exempt = True
                elif kind == "historical:start":
                    if history_start is not None:
                        self.error(path, number, "历史区段不能嵌套")
                    else:
                        history_start = number
                elif history_start is None:
                    self.error(path, number, "历史区段 end 缺少 start")
                else:
                    history_start = None
            if match:
                info = match[2].strip().split()
                fence = (match[1][0], len(match[1]), info[0].lower() if info else "", number)
                seen_fence = True
            else:
                self.links(path, number, visible[number - 1])
        if fence:
            self.error(path, fence[3], "代码围栏未闭合")
        if history_start is not None:
            self.error(path, history_start, "历史区段 start 缺少 end")


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
            checker.error(path, 1, f"无法检查文档: {error}")
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
