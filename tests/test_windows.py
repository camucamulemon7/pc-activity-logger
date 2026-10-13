import unittest
from unittest.mock import Mock, patch

from pc_activity_logger.windows import _visible_window_rect, enable_dpi_awareness


class WindowsCaptureTests(unittest.TestCase):
    def test_prefers_physical_dwm_bounds_and_falls_back_on_failure(self) -> None:
        for dwm_result in (0, -1):
            with self.subTest(result=dwm_result):
                user32, dwm = Mock(), Mock()
                def get_dwm(_hwnd, _attribute, pointer, _size):
                    pointer._obj.left, pointer._obj.top = -800, 50
                    pointer._obj.right, pointer._obj.bottom = -300, 450
                    return dwm_result
                def get_legacy(_hwnd, pointer):
                    pointer._obj.left, pointer._obj.top = -808, 42
                    pointer._obj.right, pointer._obj.bottom = -292, 458
                    return True
                dwm.DwmGetWindowAttribute.side_effect = get_dwm
                user32.GetWindowRect.side_effect = get_legacy
                with patch("pc_activity_logger.windows.ctypes.WinDLL", return_value=dwm, create=True):
                    rect = _visible_window_rect(123, user32)
                self.assertEqual(rect.left, -800 if dwm_result == 0 else -808)
                self.assertEqual(user32.GetWindowRect.call_count, 0 if dwm_result == 0 else 1)

    def test_dpi_context_is_set_or_existing_manifest_is_respected(self) -> None:
        for succeeds, last_error in ((True, 0), (False, 5)):
            user32 = Mock()
            user32.SetProcessDpiAwarenessContext.return_value = succeeds
            with self.subTest(succeeds=succeeds), patch("pc_activity_logger.windows.sys.platform", "win32"), patch(
                "pc_activity_logger.windows.ctypes.WinDLL", return_value=user32, create=True
            ), patch("pc_activity_logger.windows.ctypes.get_last_error", return_value=last_error, create=True):
                self.assertTrue(enable_dpi_awareness())
            user32.SetProcessDpiAwarenessContext.assert_called_once()

    def test_dpi_v1_fallback_when_v2_is_unavailable(self) -> None:
        user32, shcore = Mock(), Mock()
        del user32.SetProcessDpiAwarenessContext
        shcore.SetProcessDpiAwareness.return_value = 0
        with patch("pc_activity_logger.windows.sys.platform", "win32"), patch(
            "pc_activity_logger.windows.ctypes.WinDLL", side_effect=[user32, shcore], create=True
        ):
            self.assertTrue(enable_dpi_awareness())
        shcore.SetProcessDpiAwareness.assert_called_once_with(2)
