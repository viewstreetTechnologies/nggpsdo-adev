# Copyright (c) 2026 Viewstreet Technologies
# SPDX-License-Identifier: MIT

"""Build the Windows x64 portable release using the active Python environment."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import zipfile


def build():
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise SystemExit("Build with 64-bit x64 Python on Windows.")
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from nggpsdo_adev import __version__
    name = "NGGPSDO-ADEV-Monitor"
    work = root / "build" / "windows"
    work.mkdir(parents=True, exist_ok=True)
    temp = work / "tmp"
    temp.mkdir(exist_ok=True)
    env = dict(os.environ, TEMP=str(temp), TMP=str(temp),
               PYINSTALLER_CONFIG_DIR=str(work / "cache"))
    version = tuple(int(part) for part in __version__.split(".")) + (0,)
    resource = work / "version.txt"
    strings = {"CompanyName": "Viewstreet Technologies",
               "FileDescription": "NGGPSDO ADEV Monitor",
               "FileVersion": __version__, "ProductVersion": __version__,
               "ProductName": "NGGPSDO ADEV Monitor",
               "OriginalFilename": name + ".exe",
               "LegalCopyright": "Copyright (c) 2026 Viewstreet Technologies"}
    entries = ",".join(f"StringStruct({key!r}, {value!r})" for key, value in strings.items())
    resource.write_text(
        f"VSVersionInfo(ffi=FixedFileInfo(filevers={version!r},prodvers={version!r},"
        "mask=0x3f,flags=0x0,OS=0x40004,fileType=0x1,subtype=0x0,date=(0,0)),"
        f"kids=[StringFileInfo([StringTable('040904B0',[{entries}])]),"
        "VarFileInfo([VarStruct('Translation',[1033,1200])])])", encoding="utf-8")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--onefile", "--windowed", "--noupx", "--icon",
                    str(root / "nggpsdo_adev" / "assets" / "viewstreet.ico"),
                    "--add-data", str(root / "nggpsdo_adev" / "assets") + ";nggpsdo_adev/assets",
                    "--name", name, "--paths", str(root),
                    "--version-file", str(resource), "--workpath", str(work / "pyinstaller"),
                    "--specpath", str(work), "--distpath", str(root / "dist"),
                    str(root / "packaging" / "windows_launcher.py")],
                   cwd=root, env=env, check=True)
    release = root / "dist" / f"{name}-{__version__}-Windows-x64"
    notices = release / "licenses"
    notices.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / "dist" / (name + ".exe"), release / (name + ".exe"))
    shutil.copy2(root / "LICENSE", release / "LICENSE.txt")
    shutil.copy2(root / "nggpsdo_adev" / "assets" / "README.md",
                 notices / "Viewstreet-branding.txt")
    python_root = Path(sys.base_prefix)
    shutil.copy2(python_root / "LICENSE.txt", notices / "Python-LICENSE.txt")
    # This official Python documentation includes licenses of incorporated libraries.
    shutil.copy2(python_root / "Doc" / "html" / "license.html",
                 notices / "Python-third-party-licenses.html")
    shutil.copy2(python_root / "tcl" / "tk8.6" / "license.terms",
                 notices / "Tcl-Tk-license.terms")
    pyi = importlib.metadata.distribution("pyinstaller")
    copying = next(p for p in pyi.files if str(p).endswith("licenses/COPYING.txt"))
    shutil.copy2(pyi.locate_file(copying), notices / "PyInstaller-COPYING.txt")
    (release / "README.txt").write_text(
        f"NGGPSDO ADEV Monitor {__version__}\nViewstreet Technologies\n\n"
        f"Extract this ZIP and run {name}.exe. Python installation is not required.\n"
        "Connect to the NGGPSDO hostname/IP and SSE port (normally 80).\n"
        "Choose After event ID 0 to replay retained hardware history.\n\n"
        "https://github.com/viewstreetTechnologies/nggpsdo-adev\n"
        "https://www.viewstreet.com/\n\n"
        "This build is not code-signed. Runtime notices are in licenses/.\n",
        encoding="utf-8")
    metadata = {"application_version": __version__, "python": sys.version,
                "platform": platform.platform(),
                "build_packages": {d.metadata["Name"]: d.version
                                   for d in importlib.metadata.distributions()}}
    (release / "BUILD-INFO.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    archive = root / "dist" / (release.name + ".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for path in sorted(release.rglob("*")):
            if path.is_file():
                output.write(path, path.relative_to(release))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = archive.with_suffix(".zip.sha256")
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    print(f"Release: {archive}\nSHA256: {digest}")


if __name__ == "__main__":
    build()
