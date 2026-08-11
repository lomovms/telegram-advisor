from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
import ctypes
from ctypes import wintypes

import mss
from PIL import Image


@dataclass(frozen=True)
class Screenshot:
    png_bytes: bytes
    width: int
    height: int
    source: str


class CaptureError(RuntimeError):
    pass


@dataclass(frozen=True)
class _CaptureWindow:
    title: str
    left: int
    top: int
    width: int
    height: int


def capture_telegram_window(window_title: str = "Telegram") -> Screenshot:
    try:
        import pygetwindow as gw
    except Exception as exc:
        raise CaptureError(f"pygetwindow is unavailable: {exc}") from exc

    windows: list[_CaptureWindow] = [
        _from_pygetwindow(w)
        for w in gw.getWindowsWithTitle(window_title)
        if getattr(w, "visible", True) and _looks_like_telegram_window(getattr(w, "title", ""))
    ]
    if not windows:
        windows = _find_telegram_windows_by_process()
    if not windows:
        active = gw.getActiveWindow()
        if (
            active
            and window_title.lower() in active.title.lower()
            and _looks_like_telegram_window(active.title)
        ):
            windows = [_from_pygetwindow(active)]
    if not windows:
        raise CaptureError(
            "Telegram window not found. Откройте Telegram Desktop; если окно уже открыто, "
            "проверьте, что процесс Telegram.exe запущен."
        )

    window = _best_window(windows)
    if window.width <= 0 or window.height <= 0:
        raise CaptureError("Telegram window has invalid size")

    bbox = {
        "left": max(0, int(window.left)),
        "top": max(0, int(window.top)),
        "width": int(window.width),
        "height": int(window.height),
    }
    with mss.mss() as sct:
        raw = sct.grab(bbox)
    image = Image.frombytes("RGB", raw.size, raw.rgb)
    png = BytesIO()
    image.save(png, format="PNG")
    return Screenshot(
        png_bytes=png.getvalue(),
        width=image.width,
        height=image.height,
        source=window.title,
    )


def crop_telegram_chat_area(
    screenshot: Screenshot,
    left_sidebar_width: int = 460,
    right_panel_width: int = 370,
) -> Screenshot:
    image = Image.open(BytesIO(screenshot.png_bytes)).convert("RGB")
    width, height = image.size
    left = max(0, min(left_sidebar_width, width - 1))
    right = width
    if right_panel_width > 0 and width - left - right_panel_width >= 420:
        right = width - right_panel_width
    if right - left < 420:
        return screenshot

    cropped = image.crop((left, 0, right, height))
    png = BytesIO()
    cropped.save(png, format="PNG")
    return Screenshot(
        png_bytes=png.getvalue(),
        width=cropped.width,
        height=cropped.height,
        source=f"{screenshot.source} (chat crop)",
    )


def save_debug_screenshot(screenshot: Screenshot, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"telegram-{stamp}.png"
    path.write_bytes(screenshot.png_bytes)
    return path


def _best_window(windows: list) -> object:
    def score(window: _CaptureWindow) -> tuple[int, int]:
        title = window.title
        exact_title_bonus = 1 if title.strip().lower() == "telegram" else 0
        area = window.width * window.height
        return exact_title_bonus, area

    return max(windows, key=score)


def _looks_like_telegram_window(title: str) -> bool:
    normalized = title.strip().lower()
    if not normalized:
        return False
    own_window_titles = {
        "telegram negotiation advisor",
        "advisor",
    }
    if normalized in own_window_titles:
        return False
    if "telegram negotiation advisor" in normalized:
        return False
    return "telegram" in normalized


def _from_pygetwindow(window: object) -> _CaptureWindow:
    return _CaptureWindow(
        title=str(getattr(window, "title", "")),
        left=int(getattr(window, "left", 0)),
        top=int(getattr(window, "top", 0)),
        width=int(getattr(window, "width", 0)),
        height=int(getattr(window, "height", 0)),
    )


def _find_telegram_windows_by_process() -> list[_CaptureWindow]:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    windows: list[_CaptureWindow] = []

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd: wintypes.HWND, _lparam: wintypes.LPARAM) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True

        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width <= 0 or height <= 0:
            return True

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe_name = _process_image_name(kernel32, psapi, int(pid.value), PROCESS_QUERY_LIMITED_INFORMATION)
        if exe_name.lower() != "telegram.exe":
            return True

        title = _window_title(user32, hwnd)
        windows.append(
            _CaptureWindow(
                title=title or "Telegram",
                left=max(0, int(rect.left)),
                top=max(0, int(rect.top)),
                width=width,
                height=height,
            )
        )
        return True

    if not user32.EnumWindows(EnumWindowsProc(callback), 0):
        return []
    return windows


def _window_title(user32: ctypes.WinDLL, hwnd: wintypes.HWND) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _process_image_name(
    kernel32: ctypes.WinDLL,
    psapi: ctypes.WinDLL,
    pid: int,
    access: int,
) -> str:
    handle = kernel32.OpenProcess(access, False, pid)
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buffer))
        query = getattr(kernel32, "QueryFullProcessImageNameW", None)
        if query and query(handle, 0, buffer, ctypes.byref(size)):
            return Path(buffer.value).name
        if psapi.GetModuleBaseNameW(handle, None, buffer, len(buffer)):
            return buffer.value
        return ""
    finally:
        kernel32.CloseHandle(handle)
