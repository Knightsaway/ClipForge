"""The Studio page, one part per file. StudioPage (in ui/pages.py) is built from these mixins."""
from .history import HistoryMixin
from .keyboard import KeyboardMixin
from .presets import StyleMixin
from .saving import DraftMixin

__all__ = ["DraftMixin", "HistoryMixin", "KeyboardMixin", "StyleMixin"]
