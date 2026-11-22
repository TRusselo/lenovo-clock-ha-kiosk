# PyInstaller spec: one-folder standalone build of lck for the current OS.
# Bundles our own files only. mtkclient (GPL-3.0), Cygwin's e2fsprogs (Windows), platform-tools, the unlock
# credentials, Magisk and Kiosk Satellite are downloaded at run time and never bundled.
# Build: pyinstaller lck.spec
from PyInstaller.utils.hooks import collect_dynamic_libs

datas = [
    ("manifest.json", "."),
    ("assets/linux", "assets/linux"),
    ("assets/tzdata", "assets/tzdata"),
    ("LICENSE", "."),
]

a = Analysis(
    ["lck/__main__.py"],
    pathex=["."],
    datas=datas,
    # libusb from the libusb-package wheel (LGPL-2.1, loaded dynamically), so USB works on Windows and macOS
    # without installing it separately.
    binaries=collect_dynamic_libs("libusb_package"),
    hiddenimports=["libusb_package", "usb.backend.libusb1", "usb.backend.libusb0", "usb.backend.openusb",
                   "zstandard", "zstandard.backend_c"],
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="lck", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="lck")
