"""Bundle required MinGW DLLs locally at setup, using the verified build compiler.

No external download, administrator action, global PATH change or binary replacement.
This loader check does not claim that a subsequent OpenGL render will succeed.
"""
from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path

from repair_projectm_mingw import library_for, main as repair_mingw
from projectm_dll_diagnostics import read_pe


def ensure(repository, *, load=ctypes.CDLL, repair=repair_mingw, platform=None):
    if (platform or os.name) != 'nt':
        return 0
    runtime, library = library_for(Path(repository))
    handles = []
    try:
        directories = {p.resolve().parent for p in (runtime/'install').rglob('*.dll') if p.is_file()}
        if any(not p.is_relative_to((runtime/'install').resolve()) for p in directories):
            raise ValueError('DLL directory escapes the project-local install')
        for directory in sorted(directories):
            handles.append(os.add_dll_directory(str(directory)))
        try:
            load(str(library))
            print('projectM DLL load passed with the project-local runtime bundle.')
            return 0
        except OSError:
            # The strict repair verifies build bytes, compiler version/architecture,
            # transitive imports and a fresh-process native API before success.
            imports = read_pe(library).get('imports', [])
            names = {str(i.get('name', '') if isinstance(i, dict) else i).casefold() for i in imports}
            if not any(name.startswith(('libgcc_', 'libstdc++', 'libwinpthread')) for name in names):
                raise RuntimeError('Non-MinGW projectM DLL load failure; no compiler runtime was copied')
    finally:
        for handle in reversed(handles):
            handle.close()
    print('Bundling the recorded MinGW compiler runtime into project-local install/bin...')
    return repair(['--repository', str(Path(repository).resolve()), '--apply'])


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repository',type=Path,required=True)
    args=p.parse_args(argv)
    try:
        return ensure(args.repository)
    except (OSError,ValueError,RuntimeError) as exc:
        print(f'projectM runtime bundle check failed: {exc}')
        return 2


if __name__=='__main__':
    raise SystemExit(main())
