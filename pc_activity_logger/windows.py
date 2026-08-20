from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MONITOR_DEFAULTTONEAREST = 2
DESKTOP_SWITCHDESKTOP = 0x0100
WTS_CURRENT_SERVER_HANDLE = 0
WTS_CURRENT_SESSION = 0xFFFFFFFF
WTS_CONNECT_STATE = 8
WTS_ACTIVE = 0
HMONITOR = wintypes.HANDLE


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD),
    ]


@dataclass(frozen=True)
class ActiveWindow:
    hwnd: int
    title: str
    app_name: str
    monitor: dict[str, int]
    window_rect: dict[str, int] | None = None


def _windows_apis() -> tuple[ctypes.WinDLL, ctypes.WinDLL]:
    if not hasattr(ctypes, "windll"):
        raise RuntimeError("This application can only capture windows on Windows")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = HMONITOR
    user32.GetMonitorInfoW.argtypes = [HMONITOR, ctypes.POINTER(MONITORINFO)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
    user32.GetLastInputInfo.restype = wintypes.BOOL

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetTickCount64.argtypes = []
    kernel32.GetTickCount64.restype = ctypes.c_ulonglong
    return user32, kernel32


def get_idle_seconds() -> float:
    """Return seconds elapsed since the last keyboard or mouse input."""
    user32, kernel32 = _windows_apis()
    last_input = LASTINPUTINFO()
    last_input.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(last_input)):
        raise ctypes.WinError(ctypes.get_last_error())

    # LASTINPUTINFO stores a 32-bit tick value. Unsigned subtraction handles
    # the approximately 49.7-day DWORD wraparound.
    current_tick = kernel32.GetTickCount64() & 0xFFFFFFFF
    elapsed_ms = (current_tick - last_input.dwTime) & 0xFFFFFFFF
    return elapsed_ms / 1000.0


def is_interactive_session_available() -> bool:
    """Return false when this session is disconnected or on a secure desktop."""
    if not hasattr(ctypes, "windll"):
        raise RuntimeError("This application can only capture windows on Windows")

    wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
    wtsapi32.WTSQuerySessionInformationW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    wtsapi32.WTSQuerySessionInformationW.restype = wintypes.BOOL
    wtsapi32.WTSFreeMemory.argtypes = [ctypes.c_void_p]
    wtsapi32.WTSFreeMemory.restype = None

    buffer = ctypes.c_void_p()
    returned = wintypes.DWORD()
    if not wtsapi32.WTSQuerySessionInformationW(
        WTS_CURRENT_SERVER_HANDLE,
        WTS_CURRENT_SESSION,
        WTS_CONNECT_STATE,
        ctypes.byref(buffer),
        ctypes.byref(returned),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if returned.value < ctypes.sizeof(ctypes.c_int):
            raise RuntimeError("Windows returned an invalid session state")
        state = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_int)).contents.value
    finally:
        wtsapi32.WTSFreeMemory(buffer)
    if state != WTS_ACTIVE:
        return False

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.SwitchDesktop.argtypes = [wintypes.HANDLE]
    user32.SwitchDesktop.restype = wintypes.BOOL
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    user32.CloseDesktop.restype = wintypes.BOOL
    desktop = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
    if not desktop:
        return False
    try:
        return bool(user32.SwitchDesktop(desktop))
    finally:
        user32.CloseDesktop(desktop)


def _process_name(hwnd: int) -> str:
    user32, kernel32 = _windows_apis()
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, process_id.value
    )
    if not handle:
        return "unknown"
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size)
        ):
            return Path(buffer.value).name
        return "unknown"
    finally:
        kernel32.CloseHandle(handle)


def get_active_window() -> ActiveWindow:
    user32, _kernel32 = _windows_apis()
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        raise RuntimeError("No foreground window is available")

    length = user32.GetWindowTextLengthW(hwnd)
    title_buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, title_buffer, length + 1)

    monitor_handle = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(monitor_handle, ctypes.byref(info)):
        raise ctypes.WinError()

    rect = info.rcMonitor
    window_rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(window_rect)):
        raise ctypes.WinError(ctypes.get_last_error())
    return ActiveWindow(
        hwnd=hwnd,
        title=title_buffer.value,
        app_name=_process_name(hwnd),
        monitor={
            "left": rect.left,
            "top": rect.top,
            "width": rect.right - rect.left,
            "height": rect.bottom - rect.top,
        },
        window_rect={
            "left": window_rect.left,
            "top": window_rect.top,
            "width": window_rect.right - window_rect.left,
            "height": window_rect.bottom - window_rect.top,
        },
    )
