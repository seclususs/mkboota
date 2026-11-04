from __future__ import annotations

from cli.common import handle_error
from cli.parsers import build_inspect_parser
from cli.terminal import print_inspect_card, print_structured_error


def run_inspect(argv: list[str]) -> int:
    parser = build_inspect_parser()

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = int(exc.code) if isinstance(exc.code, int) else 0
        return 0 if code == 0 else 1

    if not args.zip_path.exists():
        print_structured_error(
            f"Archive file not found: {args.zip_path}",
            why="File not found",
            fix="Verify file path and access permissions",
        )
        return 1

    try:
        if not args.quiet:
            print_inspect_card(args.zip_path)

        return 0
    except Exception as exc:
        return handle_error(exc)


__all__ = ["run_inspect"]
