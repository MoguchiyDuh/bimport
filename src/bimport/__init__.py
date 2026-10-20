import sys


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "__complete":
        from bimport.complete import run

        raise SystemExit(run(sys.argv[2:]))

    from bimport.cli import main as cli_main

    raise SystemExit(cli_main())
