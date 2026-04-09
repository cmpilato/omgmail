from typing import NoReturn

from .cli import main


def run() -> NoReturn:
    raise SystemExit(main())


run()
