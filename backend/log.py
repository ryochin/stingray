import os
import sys

_NO_COLOR = not sys.stdout.isatty() or os.environ.get("NO_COLOR") is not None

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"


def _c(code: str, msg: str, *, label: str = "") -> str:
  # Without color (e.g. `docker compose logs`, which is not a tty) the label is
  # what tells a warning or an error apart from an ordinary line.
  if _NO_COLOR:
    return f"{label}{msg}"
  return f"{code}{msg}{_RESET}"


def step(msg: str) -> None:
  print(_c(f"{_BOLD}{_CYAN}", msg))


def success(msg: str) -> None:
  print(_c(_GREEN, msg))


def info(msg: str) -> None:
  print(msg)


def warn(msg: str) -> None:
  print(_c(_YELLOW, msg, label="WARNING: "))


def error(msg: str) -> None:
  print(_c(_RED, msg, label="ERROR: "))


def dim(msg: str) -> None:
  print(_c(_DIM, msg))
