"""Entry point for the packaged ClipForge.exe (PyInstaller runs this file as __main__, so it imports
the package absolutely; clipforge/__main__.py stays for `python -m clipforge`)."""
from clipforge.app import main

if __name__ == "__main__":
    main()
