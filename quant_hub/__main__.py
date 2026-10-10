"""Entry point for ``python -m quant_hub``: the current (Qt) UI by default,
the classic Tk UI with ``--classic`` while the migration runs (D31)."""

import sys

if __name__ == "__main__":
    if "--classic" in sys.argv[1:]:
        from quant_hub.app import main
        main()
    else:
        from quant_hub.app_qt import main_qt
        main_qt()
