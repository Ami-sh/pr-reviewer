"""Tiny logging helpers (stderr, with timestamps when verbose)."""
from __future__ import annotations

import sys
import time

_VERBOSE = False
_T0 = time.time()


def set_verbose(v: bool) -> None:
    global _VERBOSE
    _VERBOSE = v


def info(msg: str) -> None:
    sys.stderr.write(f"[pr-reviewer] {msg}\n")
    sys.stderr.flush()


def debug(msg: str) -> None:
    if _VERBOSE:
        sys.stderr.write(f"[pr-reviewer +{time.time() - _T0:6.1f}s] {msg}\n")
        sys.stderr.flush()


def warn(msg: str) -> None:
    sys.stderr.write(f"[pr-reviewer] WARNING: {msg}\n")
    sys.stderr.flush()


def error(msg: str) -> None:
    sys.stderr.write(f"[pr-reviewer] ERROR: {msg}\n")
    sys.stderr.flush()
