"""PyInstaller entrypoint (absolute imports; package __main__ cannot be frozen as a script)."""
from clipforge.app import main

if __name__ == "__main__":
    main()
