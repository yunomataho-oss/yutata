"""
Base class for all drawing file extractors.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ExtractionResult:
    """Represents extracted information from a drawing file."""
    file_path: str
    file_type: str           # 'pdf', 'dxf', 'dwg', 'slddrw'
    texts: List[str] = field(default_factory=list)   # all text strings found
    drawing_numbers: List[str] = field(default_factory=list)  # detected drawing numbers
    title: Optional[str] = None
    revision: Optional[str] = None
    raw_metadata: dict = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def filename(self) -> str:
        return os.path.basename(self.file_path)

    @property
    def success(self) -> bool:
        return self.error is None

    def all_text_block(self) -> str:
        """Return all texts joined as single string for search."""
        return " ".join(self.texts)


class BaseExtractor(ABC):
    """Abstract base class for all file type extractors."""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}

    @abstractmethod
    def extract(self, file_path: str) -> ExtractionResult:
        """Extract text / drawing numbers from the given file."""
        ...

    @staticmethod
    def _safe_read(file_path: str) -> bool:
        """Check file existence and readability."""
        return os.path.isfile(file_path) and os.access(file_path, os.R_OK)
