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


def _c(code: str, msg: str) -> str:
  if _NO_COLOR:
    return msg
  return f"{code}{msg}{_RESET}"


def step(msg: str) -> None:
  print(_c(f"{_BOLD}{_CYAN}", msg))


def success(msg: str) -> None:
  print(_c(_GREEN, msg))


def info(msg: str) -> None:
  print(msg)


def warn(msg: str) -> None:
  print(_c(_YELLOW, msg))


def error(msg: str) -> None:
  print(_c(_RED, msg))


def dim(msg: str) -> None:
  print(_c(_DIM, msg))
