"""Skins. "Legacy" is the original dark navy look modelled on GG Moments; the others are inspired by the
feel of four games (colours, type and shapes only — no logos or artwork). Pick one in Settings → Look.

The module-level colour names (BG, ACCENT, …) are what the rest of the app paints with, so a skin is applied
by apply(name) before the window is built; QSS is the matching Qt stylesheet."""
from __future__ import annotations

SKINS: dict[str, dict] = {
    "legacy": {
        "label": "Legacy (ClipForge classic)",
        "BG": "#161b21", "PANEL": "#1d232b", "CARD": "#232a33", "CARD_HOVER": "#2a323c", "BORDER": "#2e3640",
        "TEXT": "#d5dce4", "MUTED": "#8a95a3", "ACCENT": "#5b5ff0", "ACCENT_HOVER": "#6c70ff", "ON_ACCENT": "#ffffff",
        "SELECTED": "#2d3150", "GREEN": "#3ddc97", "GREEN_BTN": "#1f8f62", "RED": "#ff5c6c", "REC_BG": "#3a2229",
        "REC_BORDER": "#6b2b36", "YELLOW": "#ffd23f", "SCROLL": "#39424d",
        "FONT": "'Segoe UI', 'Inter', sans-serif", "SIZE": 13, "RADIUS": 5, "H1_SPACING": 0.5, "H1_COLOR": "",
        "LOGO": "◆", "EXTRA": "", "CLASSIC": True,
    },
    "halflife": {
        "label": "Half-Life 2 — City 17 (amber HUD)",
        "BG": "#191712", "PANEL": "#201d17", "CARD": "#29251e", "CARD_HOVER": "#342e25", "BORDER": "#4a4133",
        "TEXT": "#f0deb6", "MUTED": "#a8946e", "ACCENT": "#ff9a1f", "ACCENT_HOVER": "#ffb24d", "ON_ACCENT": "#1a140c",
        "SELECTED": "#3e2f16", "GREEN": "#9bd36d", "GREEN_BTN": "#5f7f2c", "RED": "#ff5233", "REC_BG": "#3a2116",
        "REC_BORDER": "#7a3a1c", "YELLOW": "#ffd04a", "SCROLL": "#5a4c36",
        "FONT": "'Trebuchet MS', 'Verdana', sans-serif", "SIZE": 13, "RADIUS": 3, "H1_SPACING": 3, "H1_COLOR": "#ff9a1f",
        "LOGO": "◉",
        "EXTRA": """
QMainWindow, QWidget#root { background: qlineargradient(x1:0,y1:0,x2:0.35,y2:1, stop:0 #2a2216, stop:0.45 #191712, stop:1 #12100c); }
QFrame#topbar { background: transparent; border-bottom: 1px solid rgba(255,154,31,0.30); }
QLabel#h1 { font-weight: 700; }

QFrame#panel { border: 1px solid rgba(255,154,31,0.22); }
QFrame#sidebar { border-right: 2px solid rgba(255,154,31,0.35); }
QPushButton#primary { letter-spacing: 1px; }
QPushButton#nav:checked { color: #ffb24d; border-left: 3px solid #ff9a1f; }
QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #c86a0c, stop:1 #ffb24d); }
""",
    },
    "control": {
        "label": "Control Resonant — The Bureau (brutalist red)",
        "BG": "#0c0c0c", "PANEL": "#141414", "CARD": "#1b1b1b", "CARD_HOVER": "#262626", "BORDER": "#3b3b3b",
        "TEXT": "#f1f0ea", "MUTED": "#8e8d87", "ACCENT": "#e3211b", "ACCENT_HOVER": "#ff3d35", "ON_ACCENT": "#ffffff",
        "SELECTED": "#38110f", "GREEN": "#d8d8d0", "GREEN_BTN": "#2a2a2a", "RED": "#ff3d35", "REC_BG": "#2a0c0b",
        "REC_BORDER": "#e3211b", "YELLOW": "#f1f0ea", "SCROLL": "#4a4a4a",
        "FONT": "'Bahnschrift', 'DIN Alternate', 'Arial Narrow', 'Segoe UI', sans-serif", "SIZE": 13, "RADIUS": 0,
        "H1_SPACING": 5, "H1_COLOR": "#ffffff", "LOGO": "■",
        "EXTRA": """
QMainWindow, QWidget#root { background: #0c0c0c; }
QFrame#topbar { background: #0c0c0c; border-bottom: 3px solid #e3211b; }
QFrame#sidebar { background: #101010; border-right: 1px solid #f1f0ea; }

QLabel#h1 { font-weight: 700; }
QLabel#h2 { letter-spacing: 3px; }
QPushButton { letter-spacing: 1px; }
QPushButton#nav { border-radius: 0; }
QPushButton#nav:checked { background: #e3211b; color: #ffffff; }
QPushButton#green { border: 1px solid #f1f0ea; color: #f1f0ea; }
QFrame#card { border: 1px solid #2c2c2c; }
QFrame#card:hover { border: 1px solid #f1f0ea; }
QGroupBox { border: 1px solid #f1f0ea; }
QGroupBox::title { color: #e3211b; letter-spacing: 2px; }
""",
    },
    "lastjedi": {
        "label": "Star Wars: The Last Jedi — Crait (red & salt white)",
        "BG": "#0a0a0d", "PANEL": "#121217", "CARD": "#1a1a21", "CARD_HOVER": "#24242d", "BORDER": "#3a1a20",
        "TEXT": "#f4efeb", "MUTED": "#a39892", "ACCENT": "#c9102c", "ACCENT_HOVER": "#e8233f", "ON_ACCENT": "#ffffff",
        "SELECTED": "#3d0c15", "GREEN": "#e9e2dc", "GREEN_BTN": "#8a0b1f", "RED": "#ff2d45", "REC_BG": "#2e0a10",
        "REC_BORDER": "#c9102c", "YELLOW": "#f6d36b", "SCROLL": "#4a2229",
        "FONT": "'Franklin Gothic Medium', 'Segoe UI', sans-serif", "SIZE": 13, "RADIUS": 4, "H1_SPACING": 4,
        "H1_COLOR": "#f4efeb", "LOGO": "✧",
        "EXTRA": """
QMainWindow, QWidget#root { background: qradialgradient(cx:0.5, cy:1.25, radius:1.1, fx:0.5, fy:1.25,
    stop:0 #5c0a16, stop:0.45 #1a0a0e, stop:1 #0a0a0d); }
QFrame#topbar { background: transparent; border-bottom: 1px solid rgba(244,239,235,0.25); }

QFrame#sidebar { border-right: 1px solid #c9102c; }
QFrame#panel { border-top: 1px solid rgba(201,16,44,0.55); }
QPushButton#nav:checked { color: #ffffff; border-left: 3px solid #c9102c; }
QSlider::handle:horizontal { background: #f4efeb; border: 2px solid #c9102c; }
QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #7d0a1b, stop:1 #ff2d45); }
QLabel#h2 { color: #e8233f; }
""",
    },
    "eldenring": {
        "label": "Elden Ring — The Lands Between (gold & dark)",
        "BG": "#0e0c09", "PANEL": "#15120d", "CARD": "#1d1912", "CARD_HOVER": "#272118", "BORDER": "#4c4029",
        "TEXT": "#ebe1c8", "MUTED": "#a29579", "ACCENT": "#c9a34e", "ACCENT_HOVER": "#e2be69", "ON_ACCENT": "#140f08",
        "SELECTED": "#30281a", "GREEN": "#b3cf84", "GREEN_BTN": "#5c4a22", "RED": "#c9503f", "REC_BG": "#2a1611",
        "REC_BORDER": "#7a3a2c", "YELLOW": "#ecc75e", "SCROLL": "#5b4c31",
        "FONT": "'Cormorant Garamond', 'Garamond', 'Georgia', serif", "SIZE": 14, "RADIUS": 1, "H1_SPACING": 4,
        "H1_COLOR": "#e2be69", "LOGO": "✦",
        "EXTRA": """
QMainWindow, QWidget#root { background: qradialgradient(cx:0.55, cy:-0.15, radius:1.15, fx:0.55, fy:-0.15,
    stop:0 #4a3a17, stop:0.35 #1c170e, stop:1 #0b0907); }
QFrame#topbar { background: transparent; border-bottom: 1px solid rgba(201,163,78,0.45); }
QFrame#sidebar { background: rgba(21,18,13,0.85); }

QLabel#h1 { font-weight: 500; }
QFrame#panel { border: 1px solid rgba(201,163,78,0.35); }
QFrame#card { border: 1px solid rgba(201,163,78,0.18); }
QFrame#card:hover { border: 1px solid rgba(226,190,105,0.6); }
QPushButton { border: 1px solid #4c4029; }
QPushButton#primary { background: transparent; color: #e2be69; border: 1px solid #c9a34e; }
QPushButton#primary:hover { background: rgba(201,163,78,0.15); }
QPushButton#nav:checked { color: #e2be69; background: rgba(201,163,78,0.10); border-left: 2px solid #c9a34e; }
QGroupBox::title { color: #c9a34e; letter-spacing: 2px; }
""",
    },
}

NAME = "legacy"
# filled in by apply(); defaults are Legacy so importing this module never breaks
BG = PANEL = CARD = CARD_HOVER = BORDER = TEXT = MUTED = ACCENT = GREEN = RED = YELLOW = ""
LOGO = "◆"
QSS = ""


def _qss(p: dict) -> str:
    r = p["RADIUS"]
    h1c = f"color: {p['H1_COLOR']};" if p["H1_COLOR"] else ""
    return f"""
* {{ font-family: {p['FONT']}; font-size: {p['SIZE']}px; color: {p['TEXT']}; }}
QMainWindow, QWidget#root {{ background: {p['BG']}; }}
QWidget {{ background: transparent; }}
QFrame#topbar {{ background: {p['BG']}; }}
QFrame#chip {{ background: {p['PANEL']}; border-radius: {r + 1}px; }}
QFrame#sidebar {{ background: {p['PANEL']}; border-right: 1px solid {p['BORDER']}; }}
QFrame#card {{ background: {p['CARD']}; border-radius: {r + 1}px; }}
QFrame#card:hover {{ background: {p['CARD_HOVER']}; }}
QFrame#card[selected="true"] {{ background: {p['SELECTED']}; border: 2px solid {p['ACCENT']}; }}
QFrame#panel {{ background: {p['PANEL']}; border-radius: {r + 3}px; }}
QLabel#h1 {{ font-size: {p['SIZE'] + 5}px; font-weight: 600; letter-spacing: {p['H1_SPACING']}px; {h1c} }}
QLabel#h2 {{ font-size: {p['SIZE']}px; font-weight: 600; color: {p['TEXT']}; letter-spacing: 1px; }}
QLabel#muted {{ color: {p['MUTED']}; font-size: {p['SIZE'] - 2}px; letter-spacing: 0.6px; }}
QLabel#game {{ color: {p['MUTED']}; font-size: {p['SIZE'] - 2}px; letter-spacing: 1px; }}
QLabel#cardtitle {{ font-size: {p['SIZE'] + 1}px; }}
QLabel#badge {{ background: rgba(20,24,30,0.85); border-radius: 13px; padding: 3px 10px; font-size: 12px; }}
QPushButton {{ background: {p['CARD']}; border: 1px solid {p['BORDER']}; border-radius: {r}px; padding: 7px 14px; }}
QPushButton:hover {{ background: {p['CARD_HOVER']}; }}
QPushButton:disabled {{ color: {p['MUTED']}; }}
QPushButton#primary {{ background: {p['ACCENT']}; border: none; color: {p['ON_ACCENT']}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {p['ACCENT_HOVER']}; }}
QPushButton#green {{ background: {p['GREEN_BTN']}; border: none; color: white; font-weight: 600; }}
QPushButton#record {{ background: {p['REC_BG']}; border: 1px solid {p['REC_BORDER']}; color: {p['RED']}; font-weight: 600; }}
QPushButton#record[active="true"] {{ background: {p['RED']}; color: white; }}
QPushButton#icon {{ background: transparent; border: none; padding: 4px; font-size: 16px; }}
QPushButton#icon:hover {{ background: {p['CARD_HOVER']}; border-radius: 4px; }}
QPushButton#nav {{ background: transparent; border: none; text-align: left; padding: 10px 16px; font-size: {p['SIZE'] + 1}px;
    border-radius: {r + 1}px; }}
QPushButton#nav:hover {{ background: {p['CARD']}; }}
QPushButton#nav:checked {{ background: {p['CARD_HOVER']}; color: white; font-weight: 600; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {p['CARD']};
    border: 1px solid {p['BORDER']}; border-radius: {r}px; padding: 6px 8px; selection-background-color: {p['ACCENT']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {p['CARD']}; border: 1px solid {p['BORDER']};
    selection-background-color: {p['ACCENT']}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: {min(r, 3)}px; border: 1px solid {p['BORDER']};
    background: {p['CARD']}; }}
QCheckBox::indicator:checked {{ background: {p['ACCENT']}; border-color: {p['ACCENT']}; }}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {p['SCROLL']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QListWidget {{ background: {p['PANEL']}; border: 1px solid {p['BORDER']}; border-radius: {r + 1}px; outline: none; }}
QListWidget::item {{ padding: 6px; border-bottom: 1px solid {p['BORDER']}; }}
QListWidget::item:selected {{ background: {p['SELECTED']}; }}
QProgressBar {{ background: {p['CARD']}; border: none; border-radius: {min(r, 4)}px; height: 8px; text-align: center;
    font-size: 10px; }}
QProgressBar::chunk {{ background: {p['ACCENT']}; border-radius: {min(r, 4)}px; }}
QSlider::groove:horizontal {{ height: 4px; background: {p['BORDER']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 14px; margin: -6px 0; background: white; border-radius: 7px; }}
QSlider::sub-page:horizontal {{ background: {p['ACCENT']}; border-radius: 2px; }}
QMenu {{ background: {p['CARD']}; border: 1px solid {p['BORDER']}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: {min(r, 4)}px; }}
QMenu::item:selected {{ background: {p['ACCENT']}; }}
QToolTip {{ background: {p['CARD']}; border: 1px solid {p['BORDER']}; color: {p['TEXT']}; padding: 4px; }}
QTabWidget::pane {{ border: none; }}
QGroupBox {{ border: 1px solid {p['BORDER']}; border-radius: {r + 3}px; margin-top: 14px; padding: 12px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {p['MUTED']}; }}
""" + ("" if p.get("CLASSIC") else _modern(p)) + p.get("EXTRA", "")


def _modern(p: dict) -> str:
    """Extra polish for the game skins (Legacy keeps its original look exactly)."""
    return f"""
QDialog {{ background: {p['BG']}; }}
QPlainTextEdit, QTextEdit {{ background: {p['CARD']}; border: 1px solid {p['BORDER']}; }}
QComboBox QAbstractItemView {{ selection-color: {p['ON_ACCENT']}; }}
QMenu::item:selected {{ color: {p['ON_ACCENT']}; }}
QRadioButton::indicator:checked {{ background: {p['ACCENT']}; border-radius: 7px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {p['SCROLL']}; border-radius: 4px; min-width: 30px; }}
QTabBar::tab {{ background: transparent; padding: 7px 14px; color: {p['MUTED']}; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {p['TEXT']}; border-bottom: 2px solid {p['ACCENT']}; }}
QPushButton#icon {{ color: {p['TEXT']}; }}
"""


def apply(name: str = "legacy") -> str:
    """Switches the colour names + stylesheet to a skin. Returns the stylesheet."""
    global NAME, QSS, LOGO
    p = SKINS.get(name) or SKINS["legacy"]
    NAME = name if name in SKINS else "legacy"
    g = globals()
    for k, v in p.items():
        if k.isupper():
            g[k] = v
    LOGO = p["LOGO"]
    QSS = _qss(p)
    return QSS


apply("legacy")
