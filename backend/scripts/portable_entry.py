"""PyInstaller entry point (absolute import, unlike package-relative __main__)."""

from ndr.portable import main

if __name__ == "__main__":
    raise SystemExit(main())
