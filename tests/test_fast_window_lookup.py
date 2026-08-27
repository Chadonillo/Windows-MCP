from unittest.mock import MagicMock, patch

from windows_mcp.desktop.service import Desktop
from windows_mcp.desktop.views import BoundingBox, Status, Window


def _box():
    return BoundingBox(left=0, top=0, right=100, bottom=100, width=100, height=100)


def _window(name: str, handle: int):
    return Window(
        name=name,
        is_browser=False,
        depth=0,
        status=Status.NORMAL,
        bounding_box=_box(),
        handle=handle,
        process_id=handle,
    )


def test_find_window_refresh_uses_top_level_enumeration_not_full_state():
    with patch.object(Desktop, "__init__", lambda self: None):
        desktop = Desktop()
    spotify = _window("Spotify Premium", 2)
    explorer = _window("File Explorer", 1)
    desktop.desktop_state = None
    desktop.get_state = MagicMock()
    desktop.get_controls_handles = MagicMock(return_value={1, 2})
    desktop.get_windows = MagicMock(return_value=([explorer, spotify], {1, 2}))
    desktop.get_active_window = MagicMock(return_value=explorer)

    found, error = desktop._find_window_by_name("Spotify", refresh_state=True)

    assert error == ""
    assert found is spotify
    desktop.get_state.assert_not_called()
    desktop.get_controls_handles.assert_called_once_with()
    desktop.get_windows.assert_called_once_with(controls_handles={1, 2})
    desktop.get_active_window.assert_called_once_with(windows=[explorer, spotify])
