#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
C 盘安全清理工具
清理临时文件、缓存、已卸载软件的残留 AppData 和文档。
使用 --dry-run 预览，不加参数直接执行。

孤儿检测安全策略（三重防误判）：
  1. 注册表 Uninstall + AppX 包 + 白名单交叉比对
  2. 正在运行的进程路径检查 — 有进程在该目录下运行时跳过
  3. 文件修改时间检查 — 最近 60 天有活跃文件时跳过
"""

import os
import sys
import shutil
import subprocess
import argparse
import logging
import winreg
import fnmatch
from pathlib import Path
from datetime import datetime, timedelta


# ═══════════════════════════════════════════════════════════════
#  白名单（不在注册表但确实在用的常见软件）
# ═══════════════════════════════════════════════════════════════
KNOWN_LEGITIMATE_APP_DATA = {
    "openai", "chatgpt",
    "codex", "cursor", "windsurf",
    "vs code", "vscode", "code",
    "docker", "rancher desktop",
    "wsl", "windows subsystem for linux",
    "postman", "insomnia",
    "obsidian", "notion", "logseq",
    "anki", "anki desktop",
    "calibre", "calibre 7",
}

# 如果一个 AppData 文件夹在最近 N 天内还有文件被修改，判定为"在用"
RECENT_DAYS = 60
# 超过 N 天没被碰过的文件夹，更容易被判定为孤儿
STALE_DAYS = 180

# 日志
logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
log = logging.getLogger("cleaner")


# ═══════════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════════

def step(label: str):
    print()
    print(f"==> {label}")


def safe_remove(path: str, recurse: bool = True, dry_run: bool = False) -> int:
    p = Path(path)
    if not p.exists():
        log.info(f"    (不存在) {path}")
        return 0
    try:
        if p.is_file() or p.is_symlink():
            if dry_run:
                log.info(f"    [预览] {path}")
                return 1
            p.unlink(missing_ok=True)
            log.info(f"    [已清理] {path}")
            return 1
        if dry_run:
            cnt = sum(1 for _ in p.rglob("*"))
            log.info(f"    [预览] {path}  ({cnt} 个子项)")
            return cnt
        if recurse:
            shutil.rmtree(p, ignore_errors=False)
        else:
            (p.rmdir if p.is_dir() else p.unlink)()
        log.info(f"    [已清理] {path}")
        return 1
    except Exception as e:
        log.warning(f"    [跳过] {path} — {e}")
        return 0


def safe_remove_glob(pattern: str, dry_run: bool = False, days_old: int | None = None):
    p = Path(pattern)
    parent, gp = p.parent, p.name
    if not parent.exists():
        return
    cutoff = (datetime.now() - timedelta(days=days_old)).timestamp() if days_old else None
    for f in parent.glob(gp):
        if f.is_dir() or (cutoff and f.stat().st_mtime > cutoff):
            continue
        safe_remove(str(f), recurse=False, dry_run=dry_run)


# ═══════════════════════════════════════════════════════════════
#  已安装程序检测（三重来源 + 进程 + 时间）
# ═══════════════════════════════════════════════════════════════

def _read_reg_uninstall(hkey: int, key_path: str, names: set):
    try:
        with winreg.OpenKey(hkey, key_path) as key:
            i = 0
            while True:
                try:
                    sk = winreg.EnumKey(key, i); i += 1
                    with winreg.OpenKey(key, sk) as sub:
                        for vn in ("DisplayName", "Publisher", "URLInfoAbout", "DisplayIcon"):
                            try:
                                v = winreg.QueryValueEx(sub, vn)[0]
                                if v and v.strip():
                                    names.add(v.lower().strip())
                            except FileNotFoundError:
                                pass
                        try:
                            loc = winreg.QueryValueEx(sub, "InstallLocation")[0]
                            if loc and loc.strip():
                                names.add(Path(loc.strip()).name.lower())
                        except FileNotFoundError:
                            pass
                except OSError:
                    break
    except (FileNotFoundError, PermissionError):
        pass


def get_installed_win32() -> set:
    names: set = set()
    for hkey, path in [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]:
        _read_reg_uninstall(hkey, path, names)
    return names


def get_installed_appx() -> set:
    names: set = set()
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "Get-AppxPackage -AllUsers | Select-Object -ExpandProperty Name"],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode == 0:
            for line in r.stdout.strip().splitlines():
                line = line.strip()
                if line:
                    names.add(line.lower())
                    parts = line.split(".")
                    if len(parts) >= 2:
                        names.add(parts[0].lower())
                        if len(parts) >= 3:
                            names.add(f"{parts[0]}.{parts[1]}".lower())
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "Get-AppxProvisionedPackage -Online | Select-Object -ExpandProperty DisplayName"],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode == 0:
            for line in r.stdout.strip().splitlines():
                if line.strip():
                    names.add(line.strip().lower())
    except Exception:
        pass
    return names


def get_all_installed() -> set:
    s = set()
    s.update(get_installed_win32())
    s.update(get_installed_appx())
    s.update(KNOWN_LEGITIMATE_APP_DATA)
    return s


def get_running_process_paths() -> set:
    """
    获取所有正在运行的进程的可执行文件路径。
    返回去重后的小写正斜杠路径集合。
    """
    paths: set[str] = set()
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "Get-Process | Where-Object { $_.Path } "
             "| Select-Object -ExpandProperty Path"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            for line in r.stdout.strip().splitlines():
                line = line.strip()
                if line and os.path.isfile(line):
                    normalized = os.path.normpath(line.lower())
                    paths.add(normalized)
                    # 上溯到 program files、appdata 等关键目录层级
                    parts = normalized.split(os.sep)
                    for i, part in enumerate(parts):
                        if part in ("appdata", "program files", "program files (x86)",
                                    "programdata", "users"):
                            paths.add(os.sep.join(parts[:i+2]))
                            break
    except Exception:
        pass
    return paths


def _has_recent_activity(folder: Path, days: int) -> bool:
    """文件夹内是否有最近 days 天内被修改过的文件？"""
    cutoff = datetime.now().timestamp() - days * 86400
    try:
        for f in folder.rglob("*"):
            if f.is_file() and f.stat().st_mtime > cutoff:
                return True
    except Exception:
        return True  # 读不了时保守处理，假设在用
    return False


def _normalize(name: str) -> str:
    for ch in "_-.,!@#$%^&*()+={}[]|\\:;\"'<>?/~`":
        name = name.replace(ch, " ")
    return " ".join(name.lower().strip().split())


def _is_system_folder(name: str) -> bool:
    system = {
        "microsoft", "microsoft corporation", "windows", "common files",
        "temp", "temporary internet files", "inetcache", "cache",
        "application data", "cookies", "history", "local settings",
        "network service", "local service",
        "all users", "default user", "default", "public",
        "desktop", "documents", "downloads", "favorites", "fonts",
        "help", "inf", "installer", "java", ".net",
        "microsoft net", "microsoft shared", "msbuild",
        "nuget", "npm", "pip",
    }
    return name.lower().replace(" ", "") in system or name.lower() in system


def is_installed(folder_name: str, installed: set) -> bool:
    fn = folder_name.lower().strip()
    if not fn or _is_system_folder(fn):
        return True
    fn_norm = _normalize(fn)
    for prog in installed:
        if not prog:
            continue
        if fn == prog or fn in prog or prog in fn:
            return True
        pn = _normalize(prog)
        if fn_norm == pn or fn_norm in pn or pn in fn_norm:
            return True
        if fnmatch.fnmatch(fn, prog):
            return True
    return False


# ═══════════════════════════════════════════════════════════════
#  清理任务
# ═══════════════════════════════════════════════════════════════

def clean_temp(dry_run: bool):
    step("1/14 临时文件夹")
    for var in ("TEMP", "TMP"):
        t = os.environ.get(var)
        if t:
            safe_remove_glob(f"{t}\\*", dry_run=dry_run)
    sr = os.environ.get("SystemRoot", "C:\\Windows")
    safe_remove_glob(f"{sr}\\Temp\\*", dry_run=dry_run)


def clean_prefetch(dry_run: bool):
    step("2/14 预读取缓存 (Prefetch)")
    pf = Path(f"{os.environ.get('SystemRoot', 'C:\\Windows')}\\Prefetch")
    if pf.exists():
        for f in pf.glob("*.pf"):
            safe_remove(str(f), recurse=False, dry_run=dry_run)


def clean_windows_update(dry_run: bool):
    step("3/14 更新下载缓存 (SoftwareDistribution)")
    sr = os.environ.get("SystemRoot", "C:\\Windows")
    dl = Path(f"{sr}\\SoftwareDistribution\\Download")
    if not dl.exists():
        log.info(f"    (不存在) {dl}"); return
    if not dry_run:
        try:
            subprocess.run(["sc", "stop", "wuauserv"],
                           capture_output=True, text=True, timeout=30)
        except Exception:
            pass
    safe_remove_glob(f"{dl}\\*", dry_run=dry_run)
    if not dry_run:
        try:
            subprocess.run(["sc", "start", "wuauserv"],
                           capture_output=True, text=True, timeout=30)
        except Exception:
            pass


def clean_wer(dry_run: bool):
    step("4/14 错误报告 (WER)")
    for base in (os.environ.get("LOCALAPPDATA", ""),
                 os.environ.get("PROGRAMDATA", "")):
        if not base: continue
        safe_remove_glob(f"{base}\\Microsoft\\Windows\\WER\\ReportArchive\\*", dry_run=dry_run)
        safe_remove_glob(f"{base}\\Microsoft\\Windows\\WER\\ReportQueue\\*", dry_run=dry_run)


def clean_user_cache(dry_run: bool):
    step("5/14 用户应用缓存")
    local = os.environ.get("LOCALAPPDATA", "")
    if not local: return
    for d in [f"{local}\\Temp\\*",
              f"{local}\\Microsoft\\Windows\\INetCache\\*",
              f"{local}\\Microsoft\\Windows\\Explorer\\*",
              f"{local}\\Microsoft\\TerminalServerClient\\Cache\\*",
              f"{local}\\D3DSCache\\*",
              f"{local}\\Microsoft\\Windows\\Caches\\*"]:
        safe_remove_glob(d, dry_run=dry_run)


def clean_browser_cache(dry_run: bool):
    step("6/14 浏览器缓存")
    local = os.environ.get("LOCALAPPDATA", "")
    appdata = os.environ.get("APPDATA", "")
    for name, base in [("Edge", f"{local}\\Microsoft\\Edge\\User Data\\Default"),
                       ("Chrome", f"{local}\\Google\\Chrome\\User Data\\Default")]:
        bp = Path(base)
        if bp.exists():
            safe_remove_glob(f"{bp}\\Cache\\*", dry_run=dry_run)
            safe_remove_glob(f"{bp}\\Code Cache\\*", dry_run=dry_run)
    ff = Path(f"{appdata}\\Mozilla\\Firefox\\Profiles") if appdata else None
    if ff and ff.exists():
        for p in ff.iterdir():
            if p.is_dir():
                safe_remove_glob(f"{p}\\cache2\\*", dry_run=dry_run)
                safe_remove_glob(f"{p}\\startupCache\\*", dry_run=dry_run)


def clean_delivery_opt(dry_run: bool):
    step("7/14 传递优化缓存")
    sr = os.environ.get("SystemRoot", "C:\\Windows")
    do = Path(f"{sr}\\ServiceProfiles\\NetworkService"
              "\\AppData\\Local\\Microsoft\\Windows\\DeliveryOptimization\\Cache")
    if do.exists():
        safe_remove_glob(f"{do}\\*", dry_run=dry_run)


def clean_recycle_bin(dry_run: bool):
    step("8/14 回收站")
    if dry_run:
        log.info("    [预览] 执行时自动清空"); return
    try:
        import ctypes
        ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0)
        log.info("    [已清理] 回收站")
    except Exception as e:
        log.warning(f"    [跳过] 回收站 — {e}")


def clean_user_recent(dry_run: bool):
    step("9/14 最近文件列表")
    ad = os.environ.get("APPDATA", "")
    if not ad: return
    for d in [f"{ad}\\Microsoft\\Windows\\Recent\\*",
              f"{ad}\\Microsoft\\Windows\\Recent\\AutomaticDestinations\\*",
              f"{ad}\\Microsoft\\Windows\\Recent\\CustomDestinations\\*"]:
        safe_remove_glob(d, dry_run=dry_run)


def clean_icon_cache(dry_run: bool):
    step("10/14 图标缓存")
    local = os.environ.get("LOCALAPPDATA", "")
    if not local: return
    p = Path(f"{local}\\IconCache.db")
    if p.exists():
        safe_remove(str(p), recurse=False, dry_run=dry_run)
    safe_remove_glob(f"{local}\\Microsoft\\Windows\\Explorer\\iconcache_*", dry_run=dry_run)


def clean_old_logs(dry_run: bool):
    step("11/14 旧系统日志 (>7天)")
    sr = os.environ.get("SystemRoot", "C:\\Windows")
    for d in [f"{sr}\\Logs", f"{sr}\\Panther", f"{sr}\\inf"]:
        safe_remove_glob(f"{d}\\*.log", dry_run=dry_run, days_old=7)


def clean_windows_old(dry_run: bool):
    step("12/14 Windows.old")
    if Path("C:\\Windows.old").exists():
        log.info("    [提示] 设置 > 系统 > 存储 > 临时文件 中清理")
    else:
        log.info("    (不存在)")


def run_cleanmgr(dry_run: bool):
    step("13/14 磁盘清理 (CleanMgr)")
    cm = Path(f"{os.environ.get('SystemRoot', 'C:\\Windows')}\\System32\\cleanmgr.exe")
    if not cm.exists(): return
    if dry_run:
        log.info("    [预览] 执行时启动 CleanMgr"); return
    try:
        subprocess.run([str(cm), "/sagerun:1"],
                       capture_output=True, text=True, timeout=300)
        log.info("    [已运行] CleanMgr 完成")
    except Exception as e:
        log.warning(f"    [跳过] CleanMgr — {e}")


# ═══════════════════════════════════════════════════════════════
#  孤儿检测（核心改进）
# ═══════════════════════════════════════════════════════════════

def _get_orphan_candidates() -> list[Path]:
    """收集所有待评估的 AppData 文件夹候选。"""
    candidates = []
    for base_path in [os.environ.get("APPDATA", ""),
                      os.environ.get("LOCALAPPDATA", ""),
                      os.environ.get("PROGRAMDATA", "")]:
        if not base_path:
            continue
        base = Path(base_path)
        if not base.exists():
            continue
        try:
            for child in sorted(base.iterdir()):
                if child.is_dir() and not child.is_symlink() and not child.name.startswith("."):
                    if not _is_system_folder(child.name):
                        candidates.append(child)
        except PermissionError:
            continue
    return candidates


def search_orphan_appdata(dry_run: bool, aggressive: bool = False):
    """
    四层过滤识别孤儿文件夹：
      1. 白名单跳过
      2. 注册表 / AppX 交叉比对
      3. 正在运行的进程路径检查
      4. 最近 60 天文件活跃度检查
    """
    step("14/14 已卸载程序的残留文档 (孤儿检测)")

    log.info("    [比对] Win32注册表 + AppX包 + 白名单")
    log.info(f"    [进程] 正在运行的进程路径（跳过有进程活跃的目录）")
    log.info(f"    [时间] 最近 {RECENT_DAYS} 天有修改的文件 → 判定在用")

    if not aggressive:
        log.info("    [安全] 默认只列表不删除，加 --aggressive 才清理")

    # 第 1-2 层：注册表 + 白名单
    installed = get_all_installed()
    log.info(f"    (已知软件/包名: {len(installed)} 个)")

    # 第 3 层：进程路径
    log.info("    (收集运行中进程路径... 请稍候)")
    in_use_paths = get_running_process_paths()
    log.info(f"    (运行中进程衍生路径: {len(in_use_paths)} 条)")

    # 收集候选
    candidates = _get_orphan_candidates()
    if not candidates:
        log.info("    (没有可评估的 AppData 文件夹)")
        return

    # 四层过滤
    orphans = []
    for child in candidates:
        name = child.name.lower()
        child_str = str(child).lower()

        # 1. 白名单
        if name in KNOWN_LEGITIMATE_APP_DATA:
            continue

        # 2. 注册表
        if is_installed(child.name, installed):
            continue

        # 3. 进程路径：是否有运行中进程的路径包含此文件夹
        has_process = any(child_str in iup for iup in in_use_paths)
        if has_process:
            continue

        # 4. 活跃时间：最近 60 天有没有文件被改过
        if _has_recent_activity(child, RECENT_DAYS):
            continue

        orphans.append(child)

    if not orphans:
        log.info("    (未发现孤儿文件夹 — 所有 AppData 文件夹均能匹配到")
        log.info("     已安装程序，或 60 天内有活跃文件，或进程路径命中)")
        return

    # 排序：按最后活跃时间（最久的排前面）
    def _sort_key(folder: Path) -> float:
        try:
            mtimes = [f.stat().st_mtime for f in folder.rglob("*") if f.is_file()]
            return max(mtimes) if mtimes else 0
        except Exception:
            return 0

    orphans.sort(key=_sort_key)

    total_size = 0
    now_ts = datetime.now().timestamp()
    for child in orphans:
        try:
            files = list(child.rglob("*"))
            size = sum(f.stat().st_size for f in files if f.is_file())
            total_size += size
            latest = max((f.stat().st_mtime for f in files if f.is_file()), default=0)
        except Exception:
            size = 0; latest = 0

        size_str = f" ({size/1024/1024:.1f} MB)" if size > 0 else ""
        if latest > 0:
            age = int((now_ts - latest) / 86400)
            log.info(f"    [孤儿] {child}{size_str}  — {age} 天未活跃")
        else:
            log.info(f"    [孤儿] {child}{size_str}")

        if aggressive and not dry_run:
            safe_remove(str(child), recurse=True, dry_run=False)

    log.info(f"    => 共 {len(orphans)} 个孤儿, 约 {total_size/1024/1024:.1f} MB")
    log.info("    [说明] 如果有误标，将文件夹名添加到脚本顶部的")
    log.info("           KNOWN_LEGITIMATE_APP_DATA 白名单即可")
    if not aggressive:
        log.info("    [建议] 确认无误后加 -a 执行清理：python clean_cdrive.py -a")

    if aggressive and not dry_run:
        _clean_empty_program_dirs(dry_run)


def _clean_empty_program_dirs(dry_run: bool):
    installed = get_all_installed()
    skip = {"microsoft", "microsoft.net", "common files",
            "windows kits", "reference assemblies",
            "modifiablewindowsapps", "windowsapps",
            "internet explorer", "msbuild"}
    for pd in [os.environ.get("ProgramFiles", "C:\\Program Files"),
               os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")]:
        p = Path(pd)
        if not p.exists():
            continue
        try:
            for child in sorted(p.iterdir()):
                if not child.is_dir() or child.name.lower() in skip:
                    continue
                if is_installed(child.name, installed):
                    continue
                try:
                    if next(child.rglob("*"), None) is None:
                        safe_remove(str(child), recurse=False, dry_run=dry_run)
                except Exception:
                    pass
        except PermissionError:
            continue


# ═══════════════════════════════════════════════════════════════
#  磁盘用量
# ═══════════════════════════════════════════════════════════════

def show_disk_usage():
    step("C 盘用量")
    try:
        u = shutil.disk_usage("C:\\")
        log.info(f"    {u.used/1e9:.1f}GB / {u.total/1e9:.1f}GB 已用, {u.free/1e9:.1f}GB 可用")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="C 盘安全清理 — 临时文件、缓存、已卸载程序残留",
        epilog="""示例:
  clean_cdrive.py -d         预览
  clean_cdrive.py            基础清理
  clean_cdrive.py -a         基础 + 孤儿清理
  clean_cdrive.py -d -a      预览孤儿
""")
    parser.add_argument("-d", "--dry-run", action="store_true", help="预览，不删除")
    parser.add_argument("-a", "--aggressive", action="store_true", help="清理已卸载程序残留")
    parser.add_argument("--no-windows-old", action="store_true", help="不检查 Windows.old")
    args = parser.parse_args()

    mode = "预览" if args.dry_run else "执行"
    print("=" * 44)
    print(f"  Clean-CDrive — C 盘安全清理 ({mode})")
    print("=" * 44)
    if args.aggressive:
        print("  [注意] 将清理已卸载程序残留，建议先 -d 预览")
    print("=" * 44)

    tasks = [
        ("temp", clean_temp),
        ("prefetch", clean_prefetch),
        ("wu_cache", clean_windows_update),
        ("wer", clean_wer),
        ("user_cache", clean_user_cache),
        ("browser", clean_browser_cache),
        ("do_cache", clean_delivery_opt),
        ("recycle", clean_recycle_bin),
        ("recent", clean_user_recent),
        ("icon_cache", clean_icon_cache),
        ("logs", clean_old_logs),
        ("orphan", lambda dr: search_orphan_appdata(dr, args.aggressive)),
    ]
    if not args.no_windows_old:
        tasks.append(("win_old", clean_windows_old))
    tasks.append(("cleanmgr", run_cleanmgr))

    for name, fn in tasks:
        try:
            fn(args.dry_run)
        except Exception as e:
            log.warning(f"    [跳过] {name}: {e}")

    show_disk_usage()
    print()
    print("=" * 44)
    print(f"  {'清理完成!' if not args.dry_run else '预览结束'}")
    print("=" * 44)


if __name__ == "__main__":
    main()
