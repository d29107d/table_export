#!/usr/bin/env python
"""Export comparison tool: structurally compares the current core output against the
legacy artifacts left in the target directories.

Why: the legacy exporter has no source code, but it left its output behind. After
changing the export logic in ``core``, run this script to confirm that the new output
is "functionally equivalent" to the old artifacts (order-independent, structure only).

Usage:
    python tools/compare_export.py [source dir] [client dir] [server dir]

Where each directory comes from (first one wins):
    1. command line argument
    2. environment variable ``COMPARE_SRC`` / ``COMPARE_CLI`` / ``COMPARE_SRV``
    3. ``compare_paths.json`` next to this script - machine-local paths, **not
       committed**, format ``{"src": "...", "cli": "...", "srv": "..."}``

Output categories:
  - structurally identical
  - differences (missing field / extra field / different value / different top-level key)
  - stale artifacts whose source sheet was renamed (the source file name in the old
    artifact's comment no longer matches the current xlsx)
  - orphan files with no source sheet (present in the target directory, but no sheet
    in the source directory matches)
  - parse errors
"""

import os
import re
import sys
import glob
import json
import collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from concurrent.futures import ThreadPoolExecutor
from core.excel_reader import load_excel, list_excel_files
from core.lua_writer import generate_lua
from luaparse import parse_lua, Dup

#: Machine-local path file (not committed): {"src": ..., "cli": ..., "srv": ...}
LOCAL_PATHS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'compare_paths.json')

#: Files / directories in the target dir that are not compared
IGNORE_FILES = {'cfg_init.lua'}


def resolve_dir(argv_index, env_name, json_key):
    """Resolve one directory: command line > environment variable > compare_paths.json."""
    if len(sys.argv) > argv_index:
        return sys.argv[argv_index]
    from_env = os.environ.get(env_name)
    if from_env:
        return from_env
    try:
        with open(LOCAL_PATHS_FILE, encoding='utf-8') as f:
            return json.load(f).get(json_key) or ''
    except (OSError, ValueError):
        return ''


def read_text(path):
    b = open(path, 'rb').read()
    for enc in ('utf-8-sig', 'utf-8', 'gbk'):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode('utf-8', 'replace')


def header_comment(text):
    """Extract the ``--[[ source.xlsx -> sheet ]]`` comment from the first line, used to spot a renamed source sheet."""
    i = text.find('--[[', 0, 400)
    if i < 0:
        return None
    j = text.find(']]', i + 4)
    return text[i + 4:j].strip() if j > 0 else None


def cmp_val(a, b, path, out):
    if isinstance(a, Dup) or isinstance(b, Dup):
        if not (isinstance(a, Dup) and isinstance(b, Dup)) or a != b:
            out.append(('dup_mismatch', tuple(path), repr(a)[:50], repr(b)[:50]))
        return
    if isinstance(a, dict) and isinstance(b, dict):
        ka, kb = set(a.keys()), set(b.keys())
        for k in sorted(ka - kb, key=repr):
            out.append(('missing_in_new', tuple(path + [k])))
        for k in sorted(kb - ka, key=repr):
            out.append(('extra_in_new', tuple(path + [k])))
        for k in ka & kb:
            cmp_val(a[k], b[k], path + [k], out)
        return
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(('len_mismatch', tuple(path), len(a), len(b)))
        for i in range(min(len(a), len(b))):
            cmp_val(a[i], b[i], path + [i], out)
        return
    if (isinstance(a, (int, float)) and isinstance(b, (int, float))
            and not isinstance(a, bool) and not isinstance(b, bool)):
        if abs(a - b) > 1e-9:
            out.append(('num_mismatch', tuple(path), a, b))
        elif a != b:
            out.append(('num_repr', tuple(path), a, b))
        return
    if a != b:
        out.append(('val_mismatch', tuple(path), repr(a)[:60], repr(b)[:60]))


def main():
    src = resolve_dir(1, 'COMPARE_SRC', 'src')
    cli = resolve_dir(2, 'COMPARE_CLI', 'cli')
    srv = resolve_dir(3, 'COMPARE_SRV', 'srv')

    if not src:
        print('No source directory given.')
        print('Usage: python tools/compare_export.py [source dir] [client dir] [server dir]')
        print('or set COMPARE_SRC / COMPARE_CLI / COMPARE_SRV,')
        print('or write {"src": "...", "cli": "...", "srv": "..."} into '
              'tools/compare_paths.json (that file is not committed).')
        return 1

    files = sorted(list_excel_files(src))
    if not files:
        print('No xlsx in the source directory:', src)
        return 1

    with ThreadPoolExecutor(8) as ex:
        loaded = list(ex.map(lambda fp: (fp, load_excel(fp)), files))

    by_name = collections.defaultdict(list)
    for fp, tables in loaded:
        for t in tables:
            t['_src'] = os.path.basename(fp)
            by_name[t['output_filename']].append(t)

    stats = collections.Counter()
    problems = collections.defaultdict(list)
    orphans, stale, ok_files, parse_errs = [], [], [], []

    for d, tag, scope in ((cli, 'client', 'c'), (srv, 'server', 's')):
        if not os.path.isdir(d):
            continue
        for p in sorted(glob.glob(os.path.join(d, '*.lua'))):
            name = os.path.basename(p)
            if name in IGNORE_FILES:
                continue
            cands = by_name.get(name)
            if not cands:
                orphans.append(f'{tag}/{name}')
                continue
            t = cands[0]
            new_text = generate_lua(t, scope)
            if new_text is None:
                stats['new_none'] += 1
                problems[f'{tag}/{name}'].append(('new_none', t['_src'], t['sheet_name']))
                continue
            old_text = read_text(p)
            hc_old, hc_new = header_comment(old_text), header_comment(new_text)
            if hc_old and hc_new and hc_old != hc_new:
                stats['stale_renamed'] += 1
                stale.append(f'{tag}/{name}: old "{hc_old}" -> new "{hc_new}"')
                continue
            try:
                old_struct = parse_lua(old_text)
                new_struct = parse_lua(new_text)
            except Exception as e:
                stats['parse_err'] += 1
                parse_errs.append((tag, name, str(e)[:70]))
                continue
            out = []
            cmp_val(old_struct, new_struct, [], out)
            if out:
                stats['diff'] += 1
                for c in collections.Counter(o[0] for o in out):
                    stats['cat_' + c] += 1
                problems[f'{tag}/{name}'].extend(
                    [(o[0], t['_src'], t['sheet_name']) + tuple(o[1:]) for o in out[:5]])
            else:
                stats['same'] += 1
                ok_files.append(f'{tag}/{name}')

    print('== stats ==')
    for k, v in sorted(stats.items()):
        print(f'  {k}: {v}')
    print(f'  structurally identical: {stats["same"]}')

    print()
    print('== stale artifacts whose source sheet was renamed ==', len(stale))
    for s in stale:
        print('  ', s)

    print()
    print('== orphan files without a source sheet ==', len(orphans))
    for o in orphans:
        print('  ', o)

    print()
    print('== parse errors ==', len(parse_errs))
    for e in parse_errs:
        print('  ', e)

    print()
    print('== difference details ==', len(problems))
    for f, ps in problems.items():
        print(f'  {f}')
        for pp in ps[:4]:
            print('      ', pp)

    return 0 if not problems else 2


if __name__ == '__main__':
    sys.exit(main())
