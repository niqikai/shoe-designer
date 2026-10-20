"""S7 end-to-end command (thin wrapper of shoe_designer.make_shoe; same as the ``make-shoe`` entry point).

    SHOE_ASSETS_ROOT=~/Downloads uv run --extra fast python scripts/make_shoe.py "波浪纹的镂空运动鞋" --form A --out-dir reports/make_shoe/a
"""
import sys

from shoe_designer.make_shoe import main

if __name__ == "__main__":
    sys.exit(main())
