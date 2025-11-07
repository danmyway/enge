"""
Utility functions and classes for enge.

This module provides common utilities including text formatting and date/time helpers.
"""

from datetime import datetime
import os
from typing import Optional


class FormatText:
    """
    ANSI color and formatting utilities for terminal output.

    Provides constants for colors, background colors, and text formatting,
    along with a method to apply multiple formatting options at once.
    """

    # Text Colors
    BLACK = "\033[30m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    PURPLE = "\033[95m"
    CYAN = "\033[96m"
    DARKCYAN = "\033[36m"
    WHITE = "\033[97m"

    # Background Colors
    BG_BLACK = "\033[40m"
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"
    BG_MAGENTA = "\033[45m"
    BG_CYAN = "\033[46m"
    BG_WHITE = "\033[47m"
    BG_DEFAULT = "\033[49m"

    # Text Formatting
    BOLD = "\033[1m"
    DIM = "\033[90m"
    ITALIC = "\033[3m"
    UNDERLINE = "\033[4m"
    END = "\033[0m"

    @staticmethod
    def format_text(
        message: str,
        bg_color: Optional[str] = None,
        text_col: Optional[str] = None,
        bold: bool = False,
        dim: bool = False,
        italic: bool = False,
        underline: bool = False,
    ) -> str:
        """
        Apply ANSI formatting to text.

        Args:
            message: The text to format
            bg_color: Background color code (use FormatText.BG_* constants)
            text_col: Text color code (use FormatText.* color constants)
            bold: Apply bold formatting
            dim: Apply dim formatting
            italic: Apply italic formatting
            underline: Apply underline formatting

        Returns:
            Formatted string with ANSI codes

        Examples:
            >>> FormatText.format_text("Error!", text_col=FormatText.RED, bold=True)
            >>> FormatText.format_text("Success", bg_color=FormatText.BG_GREEN)
        """
        formats = []

        if bg_color:
            formats.append(bg_color)
        if text_col:
            formats.append(text_col)
        if bold:
            formats.append(FormatText.BOLD)
        if dim:
            formats.append(FormatText.DIM)
        if italic:
            formats.append(FormatText.ITALIC)
        if underline:
            formats.append(FormatText.UNDERLINE)

        if not formats:
            return message

        format_codes = "".join(formats)
        return f"{format_codes}{message}{FormatText.END}"

    @classmethod
    def colorize(cls, text: str, color: str) -> str:
        """
        Simple color application helper.

        Args:
            text: Text to colorize
            color: Color code to apply

        Returns:
            Colorized text string
        """
        return f"{color}{text}{cls.END}"

    @classmethod
    def info_text(cls, text: str) -> str:
        """Format text as info (blue)."""
        return cls.format_text(text, text_col=cls.BLUE)


def get_datetime() -> str:
    """
    Get current datetime as a formatted string.

    Returns:
        Current datetime in YYYYMMDDHHMMSS format

    Examples:
        >>> get_datetime()
        '20231215143022'
    """
    return datetime.now().strftime("%Y%m%d%H%M%S")


def get_timestamp() -> str:
    """
    Get current timestamp as a human-readable string.

    Returns:
        Current timestamp in YYYY-MM-DD HH:MM:SS format

    Examples:
        >>> get_timestamp()
        '2023-12-15 14:30:22'
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
