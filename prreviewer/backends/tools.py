"""Read-only repository tools for the API backend's agent loop.

The Claude Code backend gets these for free (Read/Grep/Glob/Bash(git ...)).
For the raw-API backend we implement the same minimal "agent-computer
interface" (cf. SWE-agent): read a file with line numbers, grep, list files,
and a few git queries.  Everything is sandboxed to the checkout directory and
read-only unless `allow_exec` is set (then `run_command` appears).
"""
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from typing import Any, Dict, List, Optional

MAX_TOOL_OUTPUT = 24_000


def _clip(s: str, n: int = MAX_TOOL_OUTPUT) -> str:
    return s if len(s) <= n else s[:n] + f"\n... [output truncated at {n} chars] ..."


class ToolBox:
    def __init__(self, repo_dir: str, allow_exec: bool = False):
        self.repo_dir = os.path.abspath(repo_dir)
        self.allow_exec = allow_exec

    # ------------------------------------------------------------ specs
    def specs(self) -> List[Dict[str, Any]]:
        """Provider-neutral tool specs: name, description, input_schema."""
        specs = [
            {
                "name": "read_file",
                "description": "Read a file from the repository checkout (the PR head). Returns the content with line numbers. Use start_line/end_line to read a slice of a large file.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "repo-relative path"},
                        "start_line": {"type": "integer", "description": "1-based first line (optional)"},
                        "end_line": {"type": "integer", "description": "1-based last line inclusive (optional)"},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "grep",
                "description": "Search file contents with an extended regular expression (like `grep -rnE`). Returns `path:line:text` matches, at most 200. Optionally restrict to a path glob such as 'src/**/*.py'.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "glob": {"type": "string", "description": "optional path glob filter"},
                        "fixed_string": {"type": "boolean", "description": "treat pattern literally"},
                        "case_insensitive": {"type": "boolean"},
                    },
                    "required": ["pattern"],
                },
            },
            {
                "name": "list_files",
                "description": "List tracked files. Optional glob (e.g. 'tests/**', '**/*.go') and a directory prefix. Returns at most 500 paths.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "glob": {"type": "string"},
                        "directory": {"type": "string"},
                    },
                    "required": [],
                },
            },
            {
                "name": "git_log",
                "description": "Recent commit history, optionally for specific paths (`git log --oneline -n N -- paths`).",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "paths": {"type": "array", "items": {"type": "string"}},
                        "n": {"type": "integer", "description": "number of commits (default 15, max 50)"},
                    },
                    "required": [],
                },
            },
            {
                "name": "git_blame",
                "description": "Who last changed a line range of a file, and in which commit.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer"},
                        "end_line": {"type": "integer"},
                    },
                    "required": ["path", "start_line", "end_line"],
                },
            },
            {
                "name": "git_show",
                "description": "Show a commit (message + stat), or a file as it was at a revision when `path` is given. Useful to compare the PR head with the base branch (`origin/<base>`).",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "rev": {"type": "string", "description": "commit sha or ref, e.g. HEAD, origin/main"},
                        "path": {"type": "string", "description": "optional file path"},
                    },
                    "required": ["rev"],
                },
            },
        ]
        if self.allow_exec:
            specs.append(
                {
                    "name": "run_command",
                    "description": "Run a shell command in the checkout (e.g. the test suite or a linter). Output is truncated. 120s timeout.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                }
            )
        return specs

    def anthropic_tools(self) -> List[Dict[str, Any]]:
        return [{"name": s["name"], "description": s["description"], "input_schema": s["input_schema"]} for s in self.specs()]

    def openai_tools(self) -> List[Dict[str, Any]]:
        return [
            {"type": "function", "function": {"name": s["name"], "description": s["description"], "parameters": s["input_schema"]}}
            for s in self.specs()
        ]

    # ------------------------------------------------------------ dispatch
    def call(self, name: str, args: Dict[str, Any]) -> str:
        try:
            fn = getattr(self, f"tool_{name}", None)
            if fn is None:
                return f"error: unknown tool {name!r}"
            return _clip(fn(**(args or {})))
        except TypeError as e:
            return f"error: bad arguments for {name}: {e}"
        except Exception as e:  # tool errors must never kill the loop
            return f"error: {type(e).__name__}: {e}"

    # ------------------------------------------------------------- impls
    def _safe_path(self, path: str) -> Optional[str]:
        full = os.path.normpath(os.path.join(self.repo_dir, path))
        if full != self.repo_dir and not full.startswith(self.repo_dir + os.sep):
            return None
        return full

    def tool_read_file(self, path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
        full = self._safe_path(path)
        if not full:
            return "error: path escapes the repository"
        if os.path.isdir(full):
            return "error: is a directory; use list_files"
        if not os.path.isfile(full):
            # help the model: suggest similarly named files
            base = os.path.basename(path)
            hits = [f for f in self._tracked() if f.endswith(base)][:10]
            return "error: file not found" + (f"; similar: {hits}" if hits else "")
        with open(full, "rb") as fh:
            data = fh.read(2_000_000)
        if b"\0" in data[:8000]:
            return "error: binary file"
        lines = data.decode("utf-8", errors="replace").splitlines()
        s = max(1, start_line or 1)
        e = min(len(lines), end_line or len(lines))
        if e - s > 400 and end_line is None:
            e = s + 399
        body = "\n".join(f"{i:5d}| {lines[i - 1]}" for i in range(s, e + 1))
        hdr = f"# {path} (lines {s}-{e} of {len(lines)})"
        return hdr + "\n" + body

    def _tracked(self) -> List[str]:
        p = subprocess.run(["git", "ls-files"], cwd=self.repo_dir, capture_output=True, text=True)
        return p.stdout.splitlines() if p.returncode == 0 else []

    def tool_grep(self, pattern: str, glob: Optional[str] = None, fixed_string: bool = False, case_insensitive: bool = False) -> str:
        args = ["git", "grep", "-n", "-I", "--no-color"]
        args.append("-F" if fixed_string else "-E")
        if case_insensitive:
            args.append("-i")
        args += ["-e", pattern, "--"]
        if glob:
            args.append(glob if "**" not in glob else ":(glob)" + glob)
        p = subprocess.run(args, cwd=self.repo_dir, capture_output=True, text=True, timeout=60)
        if p.returncode == 1 and not p.stdout:
            return "(no matches)"
        if p.returncode > 1:
            return f"error: {p.stderr.strip()}"
        lines = p.stdout.splitlines()
        if len(lines) > 200:
            lines = lines[:200] + [f"... ({len(lines) - 200} more matches; narrow the pattern or glob)"]
        return "\n".join(lines)

    def tool_list_files(self, glob: Optional[str] = None, directory: Optional[str] = None) -> str:
        files = self._tracked()
        if directory:
            d = directory.strip("/") + "/"
            files = [f for f in files if f.startswith(d)]
        if glob:
            rx = _glob_to_regex(glob)
            files = [f for f in files if rx.match(f)]
        if not files:
            return "(no files)"
        more = f"\n... ({len(files) - 500} more)" if len(files) > 500 else ""
        return "\n".join(files[:500]) + more

    def tool_git_log(self, paths: Optional[List[str]] = None, n: int = 15) -> str:
        n = max(1, min(int(n or 15), 50))
        args = ["git", "log", f"-n{n}", "--no-color", "--date=short", "--format=%h %ad %an: %s"]
        if paths:
            args += ["--"] + list(paths)
        p = subprocess.run(args, cwd=self.repo_dir, capture_output=True, text=True, timeout=60)
        return p.stdout.strip() or p.stderr.strip() or "(no history)"

    def tool_git_blame(self, path: str, start_line: int, end_line: int) -> str:
        p = subprocess.run(
            ["git", "blame", "--date=short", "-L", f"{int(start_line)},{int(end_line)}", "--", path],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return p.stdout if p.returncode == 0 else f"error: {p.stderr.strip()}"

    def tool_git_show(self, rev: str, path: Optional[str] = None) -> str:
        if not re.match(r"^[\w./@^~{}\-]+$", rev or ""):
            return "error: invalid rev"
        if path:
            p = subprocess.run(["git", "show", "--no-color", f"{rev}:{path}"], cwd=self.repo_dir, capture_output=True, text=True, timeout=60)
        else:
            p = subprocess.run(["git", "show", "--no-color", "--stat", rev], cwd=self.repo_dir, capture_output=True, text=True, timeout=60)
        return p.stdout if p.returncode == 0 else f"error: {p.stderr.strip()}"

    def tool_run_command(self, command: str) -> str:
        if not self.allow_exec:
            return "error: command execution is disabled (run with --allow-exec)"
        try:
            p = subprocess.run(command, shell=True, cwd=self.repo_dir, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return "error: timed out after 120s"
        return f"exit={p.returncode}\n--- stdout ---\n{p.stdout[-12000:]}\n--- stderr ---\n{p.stderr[-6000:]}"


def _glob_to_regex(glob: str) -> "re.Pattern[str]":
    # '**' matches across directories, '*' within a segment
    out = ""
    i = 0
    while i < len(glob):
        c = glob[i]
        if c == "*":
            if glob[i : i + 2] == "**":
                out += ".*"
                i += 2
                if i < len(glob) and glob[i] == "/":
                    i += 1
                continue
            out += "[^/]*"
        elif c == "?":
            out += "[^/]"
        else:
            out += re.escape(c)
        i += 1
    return re.compile("^" + out + "$")
