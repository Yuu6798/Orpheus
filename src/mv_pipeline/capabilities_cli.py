"""互換入口。CLI本体は :mod:`orpheus_catalog.capabilities_cli` にある。"""
from orpheus_catalog.capabilities_cli import *  # noqa: F401,F403


if __name__ == "__main__":
    raise SystemExit(main())
