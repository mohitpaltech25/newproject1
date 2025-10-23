import sys
import os
import time
import threading
import atexit
from collections import defaultdict

# Global state
_WORKSPACE_ROOT = os.path.abspath(os.getenv("WORKSPACE_ROOT", "/workspace"))
_REPORT_PATH = os.path.join(_WORKSPACE_ROOT, "logs", "profiling_report.txt")
_include_only_workspace = True
_enabled = False

# Locks and containers
_lock = threading.RLock()
_callstacks = defaultdict(list)  # tid -> list of {key, start_wall, start_cpu, child_wall, child_cpu}
_fn_stats = {}  # (relfile, qual) -> {calls, total_wall, self_wall, total_cpu, self_cpu}
_fn_stats_by_thread = defaultdict(
    lambda: defaultdict(lambda: {"calls": 0, "total_wall": 0.0, "self_wall": 0.0, "total_cpu": 0.0, "self_cpu": 0.0})
)
_thread_stats = {}  # tid -> {name, first_wall, last_wall, first_cpu, last_cpu}


def _now_wall() -> float:
    return time.perf_counter()


def _now_cpu() -> float:
    # CPU time of the current thread; monotonic and not wall time
    try:
        return time.thread_time()
    except Exception:
        # Fallback to process time if thread_time is unavailable (older Pythons)
        return time.process_time()


def _qualname_from_frame(frame) -> str:
    module = frame.f_globals.get("__name__", "")
    funcname = frame.f_code.co_name
    cls_name = None
    self_obj = frame.f_locals.get("self")
    if self_obj is not None:
        # Best-effort class detection
        try:
            cls_name = self_obj.__class__.__name__
        except Exception:
            cls_name = None
    qual = f"{module}.{(cls_name + '.') if cls_name else ''}{funcname}"
    return qual


def _func_key(frame):
    filename = os.path.abspath(frame.f_code.co_filename)
    if _include_only_workspace and not filename.startswith(_WORKSPACE_ROOT):
        return None
    relfile = os.path.relpath(filename, _WORKSPACE_ROOT) if filename.startswith(_WORKSPACE_ROOT) else filename
    qual = _qualname_from_frame(frame)
    return (relfile, qual)


def _ensure_thread_entry(tid: int, ts_wall: float, ts_cpu: float):
    with _lock:
        info = _thread_stats.get(tid)
        if info is None:
            _thread_stats[tid] = {
                "name": threading.current_thread().name,
                "first_wall": ts_wall,
                "last_wall": ts_wall,
                "first_cpu": ts_cpu,
                "last_cpu": ts_cpu,
            }
        else:
            info["last_wall"] = ts_wall
            info["last_cpu"] = ts_cpu


def _record_parent_child_time(tid: int, elapsed_wall: float, elapsed_cpu: float):
    # Attribute child time to parent (for self time computation)
    with _lock:
        stack = _callstacks.get(tid)
        if stack:
            stack[-1]["child_wall"] += elapsed_wall
            stack[-1]["child_cpu"] += elapsed_cpu


def _profile(frame, event, arg):
    # Called very frequently; keep this tight
    ts_wall = _now_wall()
    ts_cpu = _now_cpu()
    tid = threading.get_ident()

    _ensure_thread_entry(tid, ts_wall, ts_cpu)

    if event == "call":
        key = _func_key(frame)
        if key is None:
            return
        with _lock:
            _callstacks[tid].append({
                "key": key,
                "start_wall": ts_wall,
                "start_cpu": ts_cpu,
                "child_wall": 0.0,
                "child_cpu": 0.0,
            })
        return

    if event in ("return", "exception"):
        with _lock:
            stack = _callstacks.get(tid)
            if not stack:
                return
            entry = stack.pop()
        elapsed_wall = ts_wall - entry["start_wall"]
        if elapsed_wall < 0:
            elapsed_wall = 0.0
        elapsed_cpu = ts_cpu - entry["start_cpu"]
        if elapsed_cpu < 0:
            elapsed_cpu = 0.0

        self_wall = max(0.0, elapsed_wall - entry["child_wall"])
        self_cpu = max(0.0, elapsed_cpu - entry["child_cpu"])

        key = entry["key"]
        with _lock:
            stats = _fn_stats.get(key)
            if stats is None:
                stats = {"calls": 0, "total_wall": 0.0, "self_wall": 0.0, "total_cpu": 0.0, "self_cpu": 0.0}
                _fn_stats[key] = stats
            stats["calls"] += 1
            stats["total_wall"] += elapsed_wall
            stats["self_wall"] += self_wall
            stats["total_cpu"] += elapsed_cpu
            stats["self_cpu"] += self_cpu

            tstats = _fn_stats_by_thread[tid][key]
            tstats["calls"] += 1
            tstats["total_wall"] += elapsed_wall
            tstats["self_wall"] += self_wall
            tstats["total_cpu"] += elapsed_cpu
            tstats["self_cpu"] += self_cpu

        _record_parent_child_time(tid, elapsed_wall, elapsed_cpu)
        return

    # Ignore other events (c_call, c_return, c_exception)


def _write_report():
    # Avoid double write
    try:
        os.makedirs(os.path.dirname(_REPORT_PATH), exist_ok=True)
    except Exception:
        pass

    try:
        lines = []
        lines.append("MIRA Profiling Report\n")
        lines.append(f"Workspace: {_WORKSPACE_ROOT}\n")
        lines.append(f"Generated at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

        # Thread summary
        lines.append("\n== Per-thread summary ==\n")
        with _lock:
            items = sorted(_thread_stats.items(), key=lambda kv: kv[1]["first_wall"])
        for tid, info in items:
            lifetime_wall = max(0.0, info["last_wall"] - info["first_wall"])
            lifetime_cpu = max(0.0, info["last_cpu"] - info["first_cpu"])
            lines.append(
                f"- {info['name']} (tid={tid}): wall={lifetime_wall:.6f}s, cpu={lifetime_cpu:.6f}s\n"
            )

        # Group function stats by file
        from collections import defaultdict as _dd
        with _lock:
            fn_items = list(_fn_stats.items())
        by_file = _dd(list)
        for (relfile, qual), st in fn_items:
            by_file[relfile].append((qual, st))

        lines.append("\n== Per-file function timings ==\n")
        for relfile in sorted(by_file.keys()):
            lines.append(f"\nFile: {relfile}\n")
            # Sort by total wall time desc
            for qual, st in sorted(by_file[relfile], key=lambda kv: kv[1]["total_wall"], reverse=True):
                lines.append(
                    f"  {qual}: calls={st['calls']}, total_wall={st['total_wall']:.6f}s, self_wall={st['self_wall']:.6f}s, total_cpu={st['total_cpu']:.6f}s, self_cpu={st['self_cpu']:.6f}s\n"
                )

        # Top functions overall
        lines.append("\n== Top 50 functions by total wall time ==\n")
        for (relfile, qual), st in sorted(fn_items, key=lambda kv: kv[1]["total_wall"], reverse=True)[:50]:
            lines.append(
                f"  {qual} [{relfile}]: total_wall={st['total_wall']:.6f}s over {st['calls']} calls (self={st['self_wall']:.6f}s)\n"
            )

        # Per-thread top functions
        lines.append("\n== Per-thread top functions ==\n")
        with _lock:
            per_thread_items = list(_fn_stats_by_thread.items())
            th_names = {tid: info.get("name", str(tid)) for tid, info in _thread_stats.items()}
        for tid, mapping in per_thread_items:
            lines.append(f"\nThread {th_names.get(tid, str(tid))} (tid={tid})\n")
            for (relfile, qual), st in sorted(mapping.items(), key=lambda kv: kv[1]["total_wall"], reverse=True)[:20]:
                lines.append(
                    f"  {qual} [{relfile}]: total_wall={st['total_wall']:.6f}s over {st['calls']} calls (self={st['self_wall']:.6f}s)\n"
                )

        with open(_REPORT_PATH, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except Exception as e:
        try:
            print(f"[profiler] Failed to write report: {e}")
        except Exception:
            pass


def start_profiling(workspace_root: str = None, report_path: str = None, include_stdlib: bool = False) -> None:
    """
    Enable global profiling for all threads. Writes report at process exit.

    - workspace_root: Only include files under this path (default: /workspace)
    - report_path: File to write results (default: <workspace>/logs/profiling_report.txt)
    - include_stdlib: If True, include non-project files (noise heavy)
    """
    global _WORKSPACE_ROOT, _REPORT_PATH, _include_only_workspace, _enabled
    if _enabled:
        return
    _enabled = True

    if workspace_root:
        _WORKSPACE_ROOT = os.path.abspath(workspace_root)
    if report_path:
        _REPORT_PATH = report_path
    _include_only_workspace = not include_stdlib

    try:
        os.makedirs(os.path.dirname(_REPORT_PATH), exist_ok=True)
    except Exception:
        pass

    sys.setprofile(_profile)
    threading.setprofile(_profile)
    atexit.register(_write_report)


def stop_profiling(write_report: bool = True) -> None:
    """Disable profiling and optionally write the report immediately."""
    global _enabled
    if not _enabled:
        return
    sys.setprofile(None)
    threading.setprofile(None)
    _enabled = False
    if write_report:
        _write_report()
