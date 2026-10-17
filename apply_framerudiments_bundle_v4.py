#!/usr/bin/env python3
"""Apply the historical cumulative patch as validated text transformations.

This script is intentionally conservative: it requires a clean tracked Git
working tree, verifies all transformations in memory first, and rolls back
all touched files if validation or generation fails. Existing files' bytes
are preserved on rollback. It generates a Git-native patch from the resulting
working tree instead of trying to hand-assemble unified-diff hunks.
"""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
from collections import Counter, OrderedDict

from rebuild_framerudiments_patch_v3 import fragments


def git(root, *args, env=None, check=True):
    return subprocess.run(['git', *args], cwd=root, env=env, text=True,
                          capture_output=True, check=check)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('patch', type=Path)
    p.add_argument('--output', type=Path, default=Path('FrameRudiments-rebased.patch'))
    a = p.parse_args()
    root = Path(git(Path.cwd(), 'rev-parse', '--show-toplevel').stdout.strip()).resolve()
    src = a.patch.resolve()
    dest = a.output.resolve()
    if not src.is_file():
        raise RuntimeError(f'Missing source patch: {src}')
    if root not in dest.parents:
        raise RuntimeError('Output patch must be inside the repository')
    if git(root, 'status', '--porcelain', '--untracked-files=no').stdout.strip():
        raise RuntimeError('Tracked files have uncommitted edits. Commit/stash them first; no files touched.')
    grouped = OrderedDict()
    for path, hunks, isnew in fragments(src.read_text(encoding='utf-8')):
        grouped.setdefault(path, []).append((hunks, isnew))
    staged = {}
    originals = {}
    for path, blocks in grouped.items():
        target = root / path
        if not target.resolve().is_relative_to(root):
            raise RuntimeError(f'Unsafe path: {path}')
        exists = target.is_file()
        isnew = blocks[0][1]
        if any(flag != isnew for _, flag in blocks):
            raise RuntimeError(f'Inconsistent creation flags: {path}')
        if isnew and target.exists():
            raise RuntimeError(f'New file already exists: {path}')
        if not isnew and not exists:
            raise RuntimeError(f'Missing tracked source file: {path}')
        raw = target.read_bytes() if exists else None
        originals[path] = raw
        current = raw.decode('utf-8').replace('\r\n', '\n') if raw is not None else ''
        ops = [(old, new) for hunks, _ in blocks for old, new in hunks]
        remaining = Counter(ops)
        for old, new in ops:
            repetitions = remaining.pop((old, new), 0)
            if not repetitions:
                continue
            if not old:
                if not isnew or repetitions != 1:
                    raise RuntimeError(f'Ambiguous insertion: {path}')
                current += new
                continue
            matches = current.count(old)
            if matches != repetitions:
                raise RuntimeError(f'{path}: expected {repetitions} match(es), found {matches} for {old[:85]!r}')
            current = current.replace(old, new, repetitions)
        # Preserve working tree line endings to avoid Windows autocrlf issues.
        eol = '\r\n' if raw is not None and b'\r\n' in raw else '\n'
        staged[path] = current.replace('\n', eol).encode('utf-8')
    # All inputs validated; source mutations only begin here.
    created = [path for path, data in originals.items() if data is None]
    touched = []
    try:
        for path, data in staged.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            touched.append(path)
        # Intent-to-add permits git diff to include new files without staging content.
        if created:
            git(root, 'add', '-N', '--', *created)
        sanity = git(root, 'diff', '--check', check=False)
        if sanity.returncode:
            raise RuntimeError('git diff --check failed:\n' + sanity.stdout + sanity.stderr)
        patch = git(root, 'diff', '--binary', '--', *staged).stdout
        if not patch:
            raise RuntimeError('No changes generated')
        # The exact working-tree diff must be reversibly applicable to that tree.
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n', suffix='.patch', delete=False) as tmp:
            tmp.write(patch)
            temp_path = Path(tmp.name)
        try:
            reverse = git(root, 'apply', '--reverse', '--check', str(temp_path), check=False)
            if reverse.returncode:
                raise RuntimeError('Generated native diff failed reverse validation:\n' + reverse.stderr)
        finally:
            temp_path.unlink(missing_ok=True)
        dest.write_text(patch, encoding='utf-8', newline='\n')
        print(f'SUCCESS: {len(staged)} file transformations applied; native Git diff: {dest}')
        print('Review git diff, run CI/tests, then git add/commit; changes are not committed.')
    except BaseException:
        for path in reversed(touched):
            target = root / path
            original = originals[path]
            if original is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(original)
        if created:
            git(root, 'reset', '--quiet', '--', *created, check=False)
        dest.unlink(missing_ok=True)
        raise

if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as e:
        print('STOP: ' + str(e), file=sys.stderr)
        sys.exit(1)
