# Qt / PySide6 redistribution notice

The Windows ZIP uses the community builds of PySide6 and Qt 6.11.1 under the
GNU Lesser General Public License version 3 (LGPL-3.0-only). VoxWeave itself
remains licensed under AGPL-3.0-only. No commercial Qt license is claimed.

The Qt and PySide6 shared libraries remain separate files below
`_internal/PySide6`; they are not statically linked into `VoxWeave.exe`.
Recipients may replace those files with ABI-compatible builds of the same
modules and version. VoxWeave does not add a signature check, encryption, or
other restriction that prevents replacement. Keep a backup of the original
directory, replace the complete PySide6/Qt library set rather than individual
DLLs, and run the application from that modified directory.

The corresponding unmodified upstream source is available without charge:

- PySide6 / Shiboken 6.11.1:
  https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.1-src/
- Qt 6.11.1:
  https://download.qt.io/official_releases/qt/6.11/6.11.1/single/

The release includes the complete LGPL-3.0 and GPL-3.0 license texts plus the
license material installed by every bundled Python distribution. The source
archives above include Qt's module-specific and third-party notices. Release
publishers must keep these source links next to the binary download or mirror
the exact source archives beside it for as long as that binary is offered.

VoxWeave's release script refuses to continue when its locked PySide6 version
does not match this notice or when required license material is unavailable.
