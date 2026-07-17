"""Build-time helper: Windows VERSIONINFO resource for the frozen exes.

An exe with no version resource (no company/product/description metadata)
matches the anonymous-executable profile AV and SmartScreen heuristics score
against, and code-signing services (e.g. SignPath) require the metadata to be
present and match the release. Used by build.spec and ocr_overlay.spec via
EXE(version=...) — NOTE the PyInstaller kwarg is `version`, not `version_info`
(unknown kwargs are silently ignored).
"""

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

_COMPANY = "Sappha1"
_PRODUCT = "PuriPulyHeart+"
_COPYRIGHT = "© Sappha1 (original by salee). Licensed under AGPL-3.0."


def build_version_resource(*, version: str, name: str, description: str) -> VSVersionInfo:
    parts = [int(p) for p in version.split(".") if p.isdigit()]
    vers = tuple((parts + [0, 0, 0, 0])[:4])
    return VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=vers,
            prodvers=vers,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,  # VOS_NT_WINDOWS32
            fileType=0x1,  # VFT_APP
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",  # en-US, Unicode
                        [
                            StringStruct("CompanyName", _COMPANY),
                            StringStruct("FileDescription", description),
                            StringStruct("FileVersion", version),
                            StringStruct("InternalName", name),
                            StringStruct("LegalCopyright", _COPYRIGHT),
                            StringStruct("OriginalFilename", f"{name}.exe"),
                            StringStruct("ProductName", _PRODUCT),
                            StringStruct("ProductVersion", version),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )
