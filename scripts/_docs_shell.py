"""Static shell path checks; requires root, error() and target() from the checker."""
from __future__ import annotations

from pathlib import Path
import re
import shlex

from _docs_scan import RETIRED_SEGMENT, SEGMENT

HEREDOC = re.compile(r"<<(-?)\s*(?:'([\w-]+)'|\"([\w-]+)\"|([\w-]+))(?=$|\s|[;)&|<>])")
ENV_EXECUTABLE = re.compile(r"(?:^|/)\.venv(?:-[\w-]+)?/bin/[\w.-]+$")
PYTHON = re.compile(r"python(?:\d+(?:\.\d+)*)?$")
ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")
CONTROL_FLOW = {"if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done", "case", "esac", "function", "{", "}"}
SEPARATORS = {"(", ")", ";", "&&", "||", "|", "&"}


def placeholder(value: str) -> bool:
    return bool(re.search(r"<[^>]+>", value))


def command_args(tokens: list[str]) -> list[str]:
    """Remove assignments, known wrappers and redirections, never reading outputs."""
    args = []
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
    while args and ASSIGNMENT.match(args[0]):
        args.pop(0)
    if args and args[0] in {"env", "command", "exec"}:
        args.pop(0)
        while args and ASSIGNMENT.match(args[0]):
            args.pop(0)
    if args[:2] == ["uv", "run"]:
        args = args[2:]
        while args and args[0] in {"--no-sync", "--locked", "--frozen", "--"}:
            args.pop(0)
    return args


class ShellChecks:
    def command(self, document: Path, line: int, tokens: list[str], cwd: Path | None) -> Path | None:
        args = command_args(tokens)
        if not args:
            return cwd
        executable = args[0]
        program = Path(executable).name
        if program in CONTROL_FLOW or any("$(" in arg or "`" in arg for arg in args):
            self.error(document, line, "不支持 shell 控制流/命令替换；请拆为明确命令")
            return None
        for token in args:
            value = token.partition("=")[2] if token.startswith("--") and "=" in token else token
            if not placeholder(value) and RETIRED_SEGMENT in SEGMENT.split(value):
                self.error(document, line, f"活跃命令使用已废弃目录: {value}")
        if program in {"run_wren.sh", "load.sh"}:
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
        python = bool(PYTHON.fullmatch(program))
        if python or program in {"bash", "sh"}:
            position = 1
            while position < len(args):
                value = args[position]
                if value in {"-m", "-c"}:
                    if value == "-c" and not python:
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
        pip = program.startswith("pip") or args[:2] == ["uv", "pip"] or (python and args[1:3] == ["-m", "pip"])
        options = {"--directory": True}
        if pip:
            options.update(dict.fromkeys(("-r", "--requirement", "-c", "--constraint"), False))
        if python and args[1:4] == ["-m", "unittest", "discover"]:
            options.update(dict.fromkeys(("-s", "--start-directory", "-t", "--top-level-directory"), True))
        for position, token in enumerate(args[1:], 1):
            option, equals, value = token.partition("=")
            if pip and token.startswith(("-r", "-c")) and len(token) > 2 and not token.startswith("--"):
                option, equals, value = token[:2], "=", token[2:]
            if option in options:
                if not equals:
                    value = args[position + 1] if position + 1 < len(args) else ""
                if not value or value.startswith("--"):
                    self.error(document, line, f"{option} 缺少路径")
                else:
                    self.target(document, line, cwd, value, directory=options[option])
        return cwd

    def shell(self, document: Path, lines: list[tuple[int, str]]) -> None:
        cwd = document.parent if document.relative_to(self.root).as_posix() == "hr-demo/wren-project/AGENTS.md" else self.root
        stack, pending, start, heredoc = [], "", 0, None
        for number, text in lines:
            if heredoc:
                if (text.lstrip("\t") if heredoc[1] else text) == heredoc[0]:
                    heredoc = None
                continue
            if not pending:
                start = number
            if text.rstrip().endswith("\\"):
                pending += text.rstrip()[:-1] + " "
                continue
            logical, pending = pending + text, ""
            if logical.lstrip().startswith("$ "):
                logical = logical.lstrip()[2:]
            try:
                probe = shlex.split(logical, comments=True)
            except ValueError as error:
                self.error(document, start, f"无法解析 shell 引号: {error}")
                continue
            if any("$(" in token or "`" in token for token in probe):
                self.error(document, start, "不支持 shell 命令替换；请拆为明确命令")
                continue
            # Check actual << tokens so quoted text/comments don't open heredocs.
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
            command = []
            for token in normalized + [";"]:
                if token not in SEPARATORS:
                    command.append(token)
                    continue
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
        if pending:
            self.error(document, start, "命令续行缺少后续行")
        if heredoc:
            self.error(document, heredoc[2], f"heredoc 缺少终止符 {heredoc[0]}")
        if stack:
            self.error(document, lines[-1][0], "子 shell 缺少右括号")
