"""
Utility helpers: logging, config, file-open cross-platform.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Dict


# ── Logging ──────────────────────────────────────────────────────────────────

def get_logger(name: str = "drawing_search") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_CONFIG_PATH = os.path.join(
    os.path.expanduser("~"), ".drawing_search", "config.json"
)

DEFAULT_CONFIG: Dict[str, Any] = {
    "index_path": os.path.join(os.path.expanduser("~"), ".drawing_search", "index.json"),
    "oda_converter_path": "",        # path to ODAFileConverter executable
    "drawing_number_patterns": [],   # user-defined extra regex patterns
    "scan_recursive": True,
    "log_level": "INFO",
}


def load_config(path: str = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                user_cfg = json.load(fh)
            config.update(user_cfg)
        except Exception:
            pass
    return config


def save_config(config: Dict[str, Any], path: str = DEFAULT_CONFIG_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False, indent=2)
