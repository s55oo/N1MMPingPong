"""Write Windows version-info files for PyInstaller (--version-file).

An exe with proper version information (product, company, description,
version) looks far less suspicious to antivirus heuristics than one
without. Run before building:

    python tools/make_version_info.py
    -> build/version_n1mm_lamps.txt, build/version_pingpong_overview.txt
"""

import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from pingpong_version import __version__  # noqa: E402

PROGRAMS = {
    "n1mm_lamps": "PingPong lamp for N1MM Logger+",
    "pingpong_overview": "PingPong Overview for N1MM Logger+",
}

TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'S55OO'),
      StringStruct('FileDescription', '{desc}'),
      StringStruct('FileVersion', '{ver}'),
      StringStruct('InternalName', '{name}'),
      StringStruct('LegalCopyright', 'S55OO - https://github.com/s55oo/N1MMPingPong'),
      StringStruct('OriginalFilename', '{name}.exe'),
      StringStruct('ProductName', 'PingPong'),
      StringStruct('ProductVersion', '{ver}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main():
    parts = [int(p) for p in __version__.split(".")][:4]
    nums = tuple(parts + [0] * (4 - len(parts)))
    out_dir = os.path.join(HERE, "build")
    os.makedirs(out_dir, exist_ok=True)
    for name, desc in PROGRAMS.items():
        path = os.path.join(out_dir, "version_{}.txt".format(name))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(TEMPLATE.format(nums=nums, desc=desc, ver=__version__, name=name))
        print("wrote", path)


if __name__ == "__main__":
    main()
