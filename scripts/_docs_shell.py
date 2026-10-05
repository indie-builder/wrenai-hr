"""Static shell path checks; checker supplies root, path, error() and target()."""
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
PLACEHOLDER = re.compile(r"<[^>]+>")


def command_args(tokens: list[str]) -> list[str]:
    """Remove assignments, known wrappers and redirections, never reading outputs."""
    args = []
    tokens = iter(tokens)
    for token in tokens:
        if token in {">", ">>", "<", "2>", "2>>"}:
            next(tokens, None)
        elif token == "2>&1":
            continue
        elif re.match(r"^(?:\d*>>?|<)[^<>]", token) and not PLACEHOLDER.search(token):
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


def shell_tokens(lines: list[tuple[int, str]], error):
    remaining = iter(lines)
    for start, text in remaining:
        while text.rstrip().endswith("\\"):
            following = next(remaining, None)
            if following is None:
                error(start, "命令续行缺少后续行")
                return
            text = text.rstrip()[:-1] + " " + following[1]
        logical = text.lstrip()[2:] if text.lstrip().startswith("$ ") else text
        try:
            probe = shlex.split(logical, comments=True)
        except ValueError as failure:
            error(start, f"无法解析 shell 引号: {failure}")
            continue
        if any("$(" in token or "`" in token for token in probe):
            error(start, "不支持 shell 命令替换；请拆为明确命令")
            continue
        heredoc = None
        if any(token.startswith("<<") for token in probe):
            matches = list(HEREDOC.finditer(logical))
            if len(matches) != 1:
                error(start, "仅支持单个、字面终止符的 heredoc")
                continue
            match = matches[0]
            heredoc = (next(group for group in match.groups()[1:] if group), bool(match[1]))
            logical = logical[:match.start()] + logical[match.end():]
        try:
            lexer = shlex.shlex(logical, posix=True, punctuation_chars="();&|")
            lexer.whitespace_split = True
            tokens = [part for token in lexer for part in
                      (re.findall(r"&&|\|\||[();&|]", token) if re.fullmatch(r"[();&|]+", token) else [token])]
        except ValueError as failure:
            error(start, f"无法解析 shell: {failure}")
        else:
            yield start, tokens
        if heredoc:
            for _, body in remaining:
                if (body.lstrip("\t") if heredoc[1] else body) == heredoc[0]:
                    break
            else:
                error(start, f"heredoc 缺少终止符 {heredoc[0]}")


class ShellChecks:
    def command(self, line: int, tokens: list[str], cwd: Path | None) -> Path | None:
        args = command_args(tokens)
        if not args:
            return cwd
        executable = args[0]
        program = Path(executable).name
        if program in CONTROL_FLOW or any("$(" in arg or "`" in arg for arg in args):
            self.error(line, "不支持 shell 控制流/命令替换；请拆为明确命令")
            return None
        for token in args:
            value = token.partition("=")[2] if token.startswith("--") and "=" in token else token
            if not PLACEHOLDER.search(value) and RETIRED_SEGMENT in SEGMENT.split(value):
                self.error(line, f"活跃命令使用已废弃目录: {value}")
        if program in {"run_wren.sh", "load.sh"}:
            self.error(line, f"活跃命令使用已废弃入口: {executable}")
        elif not ENV_EXECUTABLE.search(executable) and ("/" in executable or executable.endswith((".py", ".sh"))):
            self.target(line, cwd, executable)
        if program == "cd":
            values = [value for value in args[1:] if value != "--"]
            if len(values) != 1 or values[0] == "-":
                self.error(line, "cd 需要一个明确的仓库目录")
                return None
            return self.target(line, cwd, values[0], directory=True)
        if program in {"source", "."} and len(args) > 1 and args[1].endswith((".sh", ".py")):
            self.target(line, cwd, args[1])
        python = bool(PYTHON.fullmatch(program))
        if python or program in {"bash", "sh"}:
            arguments = iter(args[1:])
            for value in arguments:
                if value in {"-m", "-c", "-"}:
                    if value == "-c" and not python:
                        self.error(line, "不支持 shell -c；请展开为明确命令")
                    break
                if value in {"-W", "-X"}:
                    next(arguments, None)
                    continue
                if value == "--":
                    value = next(arguments, None)
                    if value is None:
                        break
                elif value.startswith("-"):
                    continue
                self.target(line, cwd, value)
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
                    self.error(line, f"{option} 缺少路径")
                else:
                    self.target(line, cwd, value, directory=options[option])
        return cwd

    def shell(self, lines: list[tuple[int, str]]) -> None:
        cwd = self.path.parent if self.path.relative_to(self.root).as_posix() == "hr-demo/wren-project/AGENTS.md" else self.root
        stack = []
        for start, tokens in shell_tokens(lines, self.error):
            command = []
            for token in tokens + [";"]:
                if token not in SEPARATORS:
                    command.append(token)
                    continue
                if command:
                    cwd = self.command(start, command, cwd)
                    command = []
                if token == "(":
                    stack.append(cwd)
                elif token == ")":
                    if stack:
                        cwd = stack.pop()
                    else:
                        self.error(start, "子 shell 右括号没有对应左括号")
        if stack:
            self.error(lines[-1][0], "子 shell 缺少右括号")
