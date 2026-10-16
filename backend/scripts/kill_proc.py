#!/usr/bin/env python3
"""按 cmdline 关键字杀进程（容器无 pkill/pgrep 的替代）。"""

from __future__ import annotations

import os
import signal
import sys


def main() -> int:
    keyword = sys.argv[1] if len(sys.argv) > 1 else "api_dashboard"
    killed: list[str] = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline") as f:
                cmd = f.read()
        except OSError:
            continue
        if keyword in cmd:
            try:
                os.kill(int(entry), signal.SIGTERM)
                killed.append(entry)
            except OSError:
                pass
    print(f"killed [{keyword}]: {killed or '(none)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
