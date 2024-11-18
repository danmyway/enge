from datetime import datetime


class FormatText:
    black = "\033[30m"
    purple = "\033[95m"
    cyan = "\033[96m"
    darkcyan = "\033[36m"
    blue = "\033[94m"
    green = "\033[92m"
    yellow = "\033[93m"
    red = "\033[91m"
    bold = "\033[1m"
    end = "\033[0m"
    bg_red = "\033[41m"
    bg_green = "\033[42m"
    bg_yellow = "\033[43m"
    bg_blue = "\033[44m"
    bg_magenta = "\033[45m"
    bg_cyan = "\033[46m"
    bg_white = "\033[47m"
    bg_default = "\033[49m"
    bg_black = "\033[40m"
    bg_purple = "\033[45m"

    @staticmethod
    def format_text(message, bg_color=None, text_col=None, bold=False):
        bg = bg_color or ""
        text_c = text_col or ""
        is_bold = FormatText.bold if bold else ""
        return f"{is_bold}{bg}{text_c}{message}{FormatText.end}"


def get_datetime():
    datetime_str = datetime.now().strftime("%Y%m%d%H%M%S")
    return datetime_str
