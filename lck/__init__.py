"""Turn a stock Lenovo Smart Clock (gen 1) into a degoogled Home Assistant kiosk."""

__version__ = "0.1.8"

# Where the docs live online. Change this one line if the repository moves.
DOCS_URL = "https://github.com/TRusselo/lenovo-clock-ha-kiosk/blob/main"


def doc(path: str, section: str = "") -> str:
    """A doc named in a message: next to the program, and online."""
    where = f"{path}, {section}" if section else path
    return f"{where} (in the tool's folder, or {DOCS_URL}/{path})"
