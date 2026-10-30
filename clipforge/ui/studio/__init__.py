"""The Studio page, one part per file. StudioPage (in ui/pages.py) is built from these mixins."""
from .ai import AIMixin
from .autoedit import AutoEditMixin
from .effects import EffectsMixin
from .history import HistoryMixin
from .keyboard import KeyboardMixin
from .overlays import OverlayMixin
from .presets import StyleMixin
from .render import RenderMixin
from .saving import DraftMixin
from .squad import SquadMixin

__all__ = ["AIMixin", "AutoEditMixin", "DraftMixin", "EffectsMixin", "HistoryMixin", "KeyboardMixin", "OverlayMixin",
           "RenderMixin", "SquadMixin", "StyleMixin"]
