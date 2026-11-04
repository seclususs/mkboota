from __future__ import annotations

from cli.parsers import build_verify_parser
from cli.terminal import print_structured_error, print_verification_table
from mkboota.verifier import BootAnimationVerifier


def run_verify(argv: list[str]) -> int:
    parser = build_verify_parser()
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

    verifier = BootAnimationVerifier()
    report = verifier.verify(args.zip_path)
    if not args.quiet:
        print_verification_table(report, zip_path=args.zip_path)

    return 0 if report.is_valid else 1


__all__ = ["run_verify"]
