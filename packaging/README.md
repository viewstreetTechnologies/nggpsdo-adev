# Windows portable build

Build on Windows with x64 CPython. The first release uses Python 3.13.9 and
PyInstaller 6.22.0. Use a full Python installation with Tcl/Tk and the local HTML
documentation installed; its license documentation is included in the download.

From the repository root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r packaging/requirements-windows.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe packaging/build_windows.py
```

The build creates:

- `dist/NGGPSDO-ADEV-Monitor.exe`: single-file GUI executable.
- `dist/NGGPSDO-ADEV-Monitor-<version>-Windows-x64.zip`: portable distribution
  containing the executable, instructions, MIT license, runtime notices and build metadata.
- A matching `.zip.sha256` checksum file.

The executable includes Python and Tcl/Tk, requires no Python installation on the
target machine, and extracts its runtime into a temporary directory at startup.
It does not install itself, request administrator privileges, or create shortcuts.
Host history remains in the same per-user location as the source application.

Version resources identify Viewstreet Technologies as the company and use the
application version from `nggpsdo_adev/__init__.py`. Builds are unsigned; code
signing requires a separate publisher certificate and signing process.

Before publishing, test the executable's GUI, hardware connection, history replay,
stability plots, time plot, and file operations. A separate Windows machine without
Python should be used for final compatibility validation. Publish the ZIP and
checksum as GitHub Release assets; do not commit compiled binaries to the source
repository. Tag the exact source commit used for the build.

The application remains MIT-licensed. Bundled components retain their own terms;
the build copies the Python license and third-party acknowledgements, Tcl/Tk
license, and PyInstaller license (including its executable distribution exception).
