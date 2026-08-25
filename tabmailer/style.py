"""Design tokens for the report workbook.

Every value here was lifted from the reference workbook
``24.08.2026 - SALE REPORT FOR AUG 26.xlsm`` so generated reports look like
they came out of the same template:

* Calibri 11 body / Calibri 14 bold title, report date top-right
* header band in Accent1 (``5B9BD5``) or its Lighter-80% tint (``DEEBF7``)
* group rows Lighter-80%, subtotals Lighter-60% (``BDD7EE``), grand total grey
* accounting number formats, thin borders, gridlines off, frozen header
"""

from __future__ import annotations

from dataclasses import dataclass, field

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

# --------------------------------------------------------------------------
# Colour themes.  ``accent`` is the solid header band; the tints are the
# standard Excel "Lighter 80/60/40%" steps of that accent, which is exactly
# what the reference workbook uses.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Theme:
    name: str
    accent: str  # solid header band
    light80: str  # group rows / soft header band
    light60: str  # subtotal rows
    light40: str  # secondary emphasis
    secondary: str  # second measure group (orange in the reference)
    secondary_light: str


THEMES: dict[str, Theme] = {
    # Office Accent1 - the reference workbook's palette
    "Blue": Theme("Blue", "5B9BD5", "DEEBF7", "BDD7EE", "9DC3E6", "ED7D31", "FBE5D6"),
    # Office Accent5
    "Indigo": Theme("Indigo", "4472C4", "D9E2F3", "B4C7E7", "8EAADB", "ED7D31", "FBE5D6"),
    # Office Accent6
    "Green": Theme("Green", "70AD47", "E2EFD9", "C5E0B4", "A9D18E", "ED7D31", "FBE5D6"),
    # Office Accent2
    "Orange": Theme("Orange", "ED7D31", "FBE5D6", "F8CBAD", "F4B183", "4472C4", "D9E2F3"),
    # Office Accent4
    "Amber": Theme("Amber", "BF8F00", "FFF2CC", "FFE699", "FFD966", "4472C4", "D9E2F3"),
    "Slate": Theme("Slate", "44546A", "D6DCE4", "ACB9CA", "8496B0", "ED7D31", "FBE5D6"),
}

DEFAULT_THEME = "Blue"

# Neutrals ------------------------------------------------------------------
WHITE = "FFFFFF"
BLACK = "000000"
GRAND_TOTAL_FILL = "D9D9D9"  # White Background 1, Darker 15%
BAND_FILL = "F7F9FC"  # very soft banding for long tables
GRID_LINE = "BFBFBF"
SUBTITLE_TEXT = "7F7F7F"

BODY_FONT = "Calibri"
BODY_SIZE = 11
TITLE_SIZE = 14

# Number formats ------------------------------------------------------------
# The reference workbook uses the built-in Excel "Comma" (accounting) styles.
ACCT_0 = r'_(* #,##0_);_(* \(#,##0\);_(* "-"_);_(@_)'
ACCT_2 = r'_(* #,##0.00_);_(* \(#,##0.00\);_(* "-"??_);_(@_)'
ACCT_0_RED = r'_(* #,##0_);[Red]_(* \(#,##0\);_(* "-"_);_(@_)'
ACCT_2_RED = r'_(* #,##0.00_);[Red]_(* \(#,##0.00\);_(* "-"??_);_(@_)'
PCT_1 = "0.0%"
PCT_2 = "0.00%"
INR_2 = r'_("₹"* #,##0.00_);_("₹"* \(#,##0.00\);_("₹"* "-"??_);_(@_)'
INR_0 = r'_("₹"* #,##0_);_("₹"* \(#,##0\);_("₹"* "-"_);_(@_)'
DATE_FMT = "dd-mmm-yyyy"
DATETIME_FMT = "dd-mmm-yyyy hh:mm"
TITLE_DATE_FMT = "dd-mmm-yyyy"

# Layout --------------------------------------------------------------------
HEADER_ROW_HEIGHT = 42.0
TITLE_ROW_HEIGHT = 20.0
SPACER_ROW_HEIGHT = 6.0
MIN_COL_WIDTH = 9.0
MAX_COL_WIDTH = 45.0
LABEL_COL_MIN_WIDTH = 14.0
ZOOM = 90

_THIN = Side(style="thin", color=GRID_LINE)
_MEDIUM = Side(style="medium", color=GRID_LINE)


@dataclass
class StyleKit:
    """Pre-built openpyxl style objects for one theme.

    openpyxl copies style objects onto each cell, so building them once and
    reusing them keeps large sheets fast and the file small.
    """

    theme: Theme
    negatives_red: bool = False
    banded: bool = False

    title_font: Font = field(init=False)
    date_font: Font = field(init=False)
    subtitle_font: Font = field(init=False)
    header_font: Font = field(init=False)
    header_fill: PatternFill = field(init=False)
    body_font: Font = field(init=False)
    label_font: Font = field(init=False)
    subtotal_font: Font = field(init=False)
    subtotal_fill: PatternFill = field(init=False)
    group_fill: PatternFill = field(init=False)
    total_font: Font = field(init=False)
    total_fill: PatternFill = field(init=False)
    band_fill: PatternFill = field(init=False)
    cell_border: Border = field(init=False)
    header_border: Border = field(init=False)
    top_rule: Border = field(init=False)
    header_align: Alignment = field(init=False)
    label_align: Alignment = field(init=False)
    value_align: Alignment = field(init=False)

    def __post_init__(self) -> None:
        t = self.theme
        self.title_font = Font(name=BODY_FONT, size=TITLE_SIZE, bold=True, color=BLACK)
        self.date_font = Font(name=BODY_FONT, size=TITLE_SIZE, bold=True, color=BLACK)
        self.subtitle_font = Font(name=BODY_FONT, size=9, italic=True, color=SUBTITLE_TEXT)
        self.header_font = Font(name=BODY_FONT, size=BODY_SIZE, bold=True, color=WHITE)
        self.header_fill = PatternFill("solid", fgColor=t.accent)
        self.body_font = Font(name=BODY_FONT, size=BODY_SIZE, color=BLACK)
        self.label_font = Font(name=BODY_FONT, size=BODY_SIZE, color=BLACK)
        self.subtotal_font = Font(name=BODY_FONT, size=BODY_SIZE, bold=True, color=BLACK)
        self.subtotal_fill = PatternFill("solid", fgColor=t.light60)
        self.group_fill = PatternFill("solid", fgColor=t.light80)
        self.total_font = Font(name=BODY_FONT, size=BODY_SIZE, bold=True, color=BLACK)
        self.total_fill = PatternFill("solid", fgColor=GRAND_TOTAL_FILL)
        self.band_fill = PatternFill("solid", fgColor=BAND_FILL)
        self.cell_border = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
        self.header_border = Border(left=_THIN, right=_THIN, top=_MEDIUM, bottom=_MEDIUM)
        self.top_rule = Border(top=_MEDIUM)
        self.header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        self.label_align = Alignment(horizontal="left", vertical="center")
        self.value_align = Alignment(horizontal="right", vertical="center")

    # -- number format helpers ---------------------------------------------
    def acct(self, decimals: bool) -> str:
        if self.negatives_red:
            return ACCT_2_RED if decimals else ACCT_0_RED
        return ACCT_2 if decimals else ACCT_0
