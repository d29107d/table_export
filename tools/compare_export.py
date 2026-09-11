#!/usr/bin/env python
"""导表对拍工具：把当前 core 的导出结果与目标目录里的旧产物做结构化对比。

用途：旧导表工具没有源码，但它在目标目录留下了产物。改动 core 的导出逻辑后，
用这个脚本确认新输出与旧产物「功能等价」（顺序无关，只看数据结构）。

用法：
    python tools/compare_export.py [源目录] [客户端目录] [服务端目录]

三个目录的取值优先级（前者优先）：
    1. 命令行参数
    2. 环境变量 ``COMPARE_SRC`` / ``COMPARE_CLI`` / ``COMPARE_SRV``
    3. 脚本同目录下的 ``compare_paths.json``——本机路径，**不入库**，
       格式 ``{"src": "...", "cli": "...", "srv": "..."}``

输出分类：
  - 结构完全一致
  - 差异（缺字段 / 多字段 / 值不同 / 顶层 key 不同）
  - 源表已改名的陈旧产物（旧产物注释里的源文件名与当前 xlsx 不一致）
  - 无源表的孤儿文件（目标目录里存在但源目录已没有对应 sheet）
  - 解析错误
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

#: 本机路径文件（不入库）：{"src": ..., "cli": ..., "srv": ...}
LOCAL_PATHS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'compare_paths.json')

#: 目标目录里不参与对比的文件/目录
IGNORE_FILES = {'cfg_init.lua'}


def resolve_dir(argv_index, env_name, json_key):
    """按 命令行参数 > 环境变量 > compare_paths.json 的顺序取一个目录。"""
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
    """取首行里的 ``--[[ 源.xlsx -> 页 ]]`` 注释，用于识别源表改名。"""
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
        print('未指定源目录。')
        print('用法: python tools/compare_export.py [源目录] [客户端目录] [服务端目录]')
        print('或设置环境变量 COMPARE_SRC / COMPARE_CLI / COMPARE_SRV，')
        print('或在 tools/compare_paths.json 里写 '
              '{"src": "...", "cli": "...", "srv": "..."}（该文件不入库）。')
        return 1

    files = sorted(list_excel_files(src))
    if not files:
        print('源目录里没有 xlsx：', src)
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
                stale.append(f'{tag}/{name}: 旧「{hc_old}」-> 新「{hc_new}」')
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

    print('== 统计 ==')
    for k, v in sorted(stats.items()):
        print(f'  {k}: {v}')
    print(f'  结构完全一致: {stats["same"]} 个')

    print()
    print('== 源表已改名的陈旧产物 ==', len(stale))
    for s in stale:
        print('  ', s)

    print()
    print('== 无源表的孤儿文件 ==', len(orphans))
    for o in orphans:
        print('  ', o)

    print()
    print('== 解析错误 ==', len(parse_errs))
    for e in parse_errs:
        print('  ', e)

    print()
    print('== 差异明细 ==', len(problems))
    for f, ps in problems.items():
        print(f'  {f}')
        for pp in ps[:4]:
            print('      ', pp)

    return 0 if not problems else 2


if __name__ == '__main__':
    sys.exit(main())
