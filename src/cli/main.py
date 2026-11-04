from __future__ import annotations

from pathlib import Path
import sys

_src_dir = Path(__file__).resolve().parent.parent
_repo_root = _src_dir.parent

if _src_dir.exists() and str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from cli.commands import (
    run_compile,
    run_inspect,
    run_repair,
    run_studio,
    run_verify,
)
from cli.parsers import (
    build_inspect_parser,
    build_parser,
    build_repair_parser,
    build_studio_parser,
    build_verify_parser,
    has_graphical_display,
    parse_resolution,
    resolve_output_path,
)


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:]) if argv is None else list(argv)
    if not args_list:
        parser = build_parser()
        parser.print_help()
        return 0

    subcommand = args_list[0]
    if subcommand == "repair":
        return run_repair(args_list[1:])
    if subcommand == "verify":
        return run_verify(args_list[1:])
    if subcommand == "inspect":
        return run_inspect(args_list[1:])
    if subcommand == "studio":
        return run_studio(args_list[1:])
    if subcommand == "compile":
        return run_compile(args_list[1:])

    return run_compile(args_list)


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "build_inspect_parser",
    "build_parser",
    "build_repair_parser",
    "build_studio_parser",
    "build_verify_parser",
    "has_graphical_display",
    "main",
    "parse_resolution",
    "resolve_output_path",
]
