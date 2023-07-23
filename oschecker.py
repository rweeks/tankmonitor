import sys

__is_macos = None


def is_macos() -> bool:
    global __is_macos
    if __is_macos is None:
        __is_macos = sys.platform == "darwin"
    return __is_macos
