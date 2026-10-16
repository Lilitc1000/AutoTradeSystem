"""统一日志配置（进程骨架用）。"""

from __future__ import annotations

import logging
import sys


def setup_logging(process_name: str, level: str = "INFO") -> logging.Logger:
    """配置根日志并返回进程 logger。

    格式：ISO 时间 + 级别 + 进程名 + 消息，输出到 stdout（容器日志收集）。
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    logger = logging.getLogger(process_name)
    logger.setLevel(level.upper())
    return logger
