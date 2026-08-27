"""Input tools — Click, Type, Scroll, Move, Shortcut, Wait, WaitFor."""

import json
import math
import time
from collections.abc import Callable, Iterator
from typing import Annotated, Any, Literal

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, StrictInt, TypeAdapter, model_validator
from windows_mcp.infrastructure import with_analytics
from fastmcp import Context


_BATCH_ACTION_TYPES = {
    "click",
    "move",
    "type",
    "shortcut",
    "scroll",
    "wait",
    "switch_window",
}
_MAX_BATCH_ACTIONS = 25
_MAX_BATCH_WAIT_SECONDS = 30.0


class _BatchBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _TargetedBatchAction(_BatchBase):
    loc: list[StrictInt] | None = None
    label: StrictInt | None = None

    @model_validator(mode="after")
    def _exactly_one_target(self):
        if (self.loc is None) == (self.label is None):
            raise ValueError("exactly one of loc or label is required")
        if self.loc is not None:
            _as_point(self.loc, "loc")
        if self.label is not None and self.label < 0:
            raise ValueError("label must be non-negative")
        return self


class _ClickBatchAction(_TargetedBatchAction):
    type: Literal["click"]
    button: Literal["left", "right", "middle"] = "left"
    clicks: Literal[0, 1, 2] = 1


class _MoveBatchAction(_TargetedBatchAction):
    type: Literal["move"]
    drag: bool = False
    from_loc: list[StrictInt] | None = None
    duration: float | None = None

    @model_validator(mode="after")
    def _drag_options(self):
        if self.from_loc is not None:
            _as_point(self.from_loc, "from_loc")
        if not self.drag and (self.from_loc is not None or self.duration is not None):
            raise ValueError("from_loc and duration require drag=true")
        if self.duration is not None:
            _validate_finite_number(self.duration, "duration")
            if self.duration < 0 or self.duration > 30:
                raise ValueError("duration must be between 0 and 30 seconds")
        return self


class _TypeBatchAction(_TargetedBatchAction):
    type: Literal["type"]
    text: str
    clear: bool = False
    caret_position: Literal["start", "idle", "end"] = "idle"
    press_enter: bool = False


class _ShortcutBatchAction(_BatchBase):
    type: Literal["shortcut"]
    shortcut: str = Field(min_length=1)


class _ScrollBatchAction(_BatchBase):
    type: Literal["scroll"]
    loc: list[StrictInt] | None = None
    label: StrictInt | None = None
    axis: Literal["vertical", "horizontal"] = "vertical"
    direction: Literal["up", "down", "left", "right"] = "down"
    wheel_times: StrictInt = Field(default=1, ge=1, le=20)

    @model_validator(mode="after")
    def _validate_scroll(self):
        if self.loc is not None and self.label is not None:
            raise ValueError("loc and label are mutually exclusive")
        if self.loc is not None:
            _as_point(self.loc, "loc")
        if self.label is not None and self.label < 0:
            raise ValueError("label must be non-negative")
        if self.axis == "vertical" and self.direction not in {"up", "down"}:
            raise ValueError("vertical scroll direction must be up or down")
        if self.axis == "horizontal" and self.direction not in {"left", "right"}:
            raise ValueError("horizontal scroll direction must be left or right")
        return self


class _WaitBatchAction(_BatchBase):
    type: Literal["wait"]
    duration: float = Field(ge=0, le=30)


class _SwitchWindowBatchAction(_BatchBase):
    type: Literal["switch_window"]
    name: str = Field(min_length=1)


_BatchAction = Annotated[
    _ClickBatchAction
    | _MoveBatchAction
    | _TypeBatchAction
    | _ShortcutBatchAction
    | _ScrollBatchAction
    | _WaitBatchAction
    | _SwitchWindowBatchAction,
    Field(discriminator="type"),
]
_BATCH_ADAPTER = TypeAdapter(list[_BatchAction])


WaitForCondition = Literal[
    "text_exists",
    "active_window",
    "element_exists",
    "element_enabled",
    "focused_element",
]


def _resolve_label(desktop: Any, label: int) -> list[int]:
    """Resolve a UI element label to screen coordinates."""
    if desktop.desktop_state is None:
        raise ValueError("Desktop state is empty. Please call Snapshot first.")
    try:
        return list(desktop.get_coordinates_from_label(label))
    except Exception as e:
        raise ValueError(f"Failed to find element with label {label}: {e}")


def _as_bool(value: bool | str, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ValueError(f"{name} must be true or false")


def _validate_finite_number(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")


def _as_loc(value: list | str | None) -> list | None:
    """Coerce a JSON-stringified list back to a list.

    Claude Desktop strips anyOf schemas and the model serializes lists as
    strings (e.g. '[100, 200]'). Parsing here keeps the tools working.
    """
    if value is None or isinstance(value, list):
        return value
    return json.loads(value)


def _as_point(value: object, name: str) -> list[int]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{name} must be a list of exactly 2 integers [x, y]")
    parsed = []
    for item in value:
        if isinstance(item, bool):
            raise ValueError(f"{name} must contain integers, not booleans")
        if isinstance(item, int):
            parsed.append(item)
            continue
        if isinstance(item, str):
            stripped = item.strip()
            if stripped and stripped.lstrip("+-").isdigit():
                parsed.append(int(stripped))
                continue
        raise ValueError(f"{name} must contain exactly 2 integers")
    return parsed


def _text_matches(value: object | None, expected: str | None) -> bool:
    if expected is None:
        return True
    if value is None:
        return False
    return expected.casefold() in str(value).casefold()


def _metadata_text_matches(metadata: dict[str, object], expected: str | None) -> bool:
    return any(_text_matches(value, expected) for value in metadata.values())


def _iter_nodes(desktop_state: Any) -> Iterator[Any]:
    tree_state = getattr(desktop_state, "tree_state", None)
    if tree_state is None:
        return
    yield from getattr(tree_state, "interactive_nodes", [])
    yield from getattr(tree_state, "scrollable_nodes", [])


def _iter_text_sources(desktop_state: Any) -> Iterator[object]:
    active_window = getattr(desktop_state, "active_window", None)
    if active_window is not None:
        yield active_window.name

    for window in getattr(desktop_state, "windows", []):
        yield window.name

    tree_state = getattr(desktop_state, "tree_state", None)
    if tree_state is None:
        return

    for node in _iter_nodes(desktop_state):
        yield node.name
        yield node.control_type
        yield node.window_name
        for value in getattr(node, "metadata", {}).values():
            yield value

    for node in getattr(tree_state, "dom_informative_nodes", []):
        yield getattr(node, "text", "")


def _node_matches(node: Any, text: str | None, window_name: str | None) -> bool:
    metadata: dict[str, object] = getattr(node, "metadata", {})
    return (
        _text_matches(getattr(node, "name", ""), text)
        or _text_matches(getattr(node, "control_type", ""), text)
        or _metadata_text_matches(metadata, text)
    ) and _text_matches(getattr(node, "window_name", ""), window_name)


def _matches_wait_condition(
    desktop_state: Any,
    condition: WaitForCondition,
    text: str | None,
    window_name: str | None,
) -> tuple[bool, str]:
    if condition == "text_exists":
        for source in _iter_text_sources(desktop_state):
            if _text_matches(source, text):
                return True, f"text {text!r} appeared"
        return False, f"text {text!r} was absent"

    if condition == "active_window":
        expected = window_name or text
        active_window = getattr(desktop_state, "active_window", None)
        active_name = active_window.name if active_window else ""
        if _text_matches(active_name, expected):
            return True, f"active window matched {active_name!r}"
        return False, f"active window was {active_name!r}"

    if condition in {"element_exists", "element_enabled"}:
        for node in _iter_nodes(desktop_state):
            if _node_matches(node, text, window_name):
                return True, f"element matched {getattr(node, 'name', '')!r}"
        return False, "matching element was absent"

    if condition == "focused_element":
        for node in _iter_nodes(desktop_state):
            metadata = getattr(node, "metadata", {})
            if metadata.get("has_focused") and _node_matches(node, text, window_name):
                return True, f"focused element matched {getattr(node, 'name', '')!r}"
        return False, "matching focused element was absent"

    raise ValueError(f"Unsupported WaitFor condition: {condition}")


def _validate_wait_for_args(
    condition: str,
    text: str | None,
    window_name: str | None,
    timeout: float,
    interval: float,
) -> WaitForCondition:
    _validate_finite_number(timeout, "timeout")
    _validate_finite_number(interval, "interval")

    normalized = condition.strip().lower().replace("-", "_")
    aliases = {
        "text": "text_exists",
        "window": "active_window",
        "element": "element_exists",
        "enabled": "element_enabled",
        "focused": "focused_element",
    }
    normalized = aliases.get(normalized, normalized)
    valid_conditions = {
        "text_exists",
        "active_window",
        "element_exists",
        "element_enabled",
        "focused_element",
    }
    if normalized not in valid_conditions:
        raise ValueError(
            "condition must be one of: text_exists, active_window, element_exists, "
            "element_enabled, focused_element"
        )

    if timeout <= 0 or timeout > 120:
        raise ValueError("timeout must be greater than 0 and at most 120 seconds")
    if interval <= 0 or interval > 5:
        raise ValueError("interval must be greater than 0 and at most 5 seconds")

    if normalized == "text_exists" and not text:
        raise ValueError("text is required when condition is text_exists")
    if normalized == "active_window" and not (text or window_name):
        raise ValueError("text or window_name is required when condition is active_window")
    if normalized in {"element_exists", "element_enabled"} and not (text or window_name):
        raise ValueError(
            "text or window_name is required when condition is element_exists or element_enabled"
        )

    return normalized


def register(
    mcp: Any,
    *,
    get_desktop: Callable[[], Any],
    get_analytics: Callable[[], Any],
) -> None:
    @mcp.tool(
        name="Click",
        description=(
            "Performs mouse clicks at specified coordinates [x, y] or passing a UI element's label/id. "
            "Supports button types: 'left' for selection/activation, 'right' for context menus, 'middle'. "
            "Supports clicks: 0=hover only (no click), 1=single click (select/focus), 2=double click (open/activate). "
            "Provide either loc or label."
        ),
        annotations=ToolAnnotations(
            title="Click",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Click-Tool")
    def click_tool(
        loc: list[int] | str | None = None,
        label: int | None = None,
        button: Literal["left", "right", "middle"] = "left",
        clicks: int = 1,
        ctx: Context = None,
    ) -> str:
        desktop = get_desktop()
        loc = _as_loc(loc)
        if loc is None and label is None:
            raise ValueError("Either loc or label must be provided.")
        if label is not None:
            loc = _resolve_label(desktop, label)
        if len(loc) != 2:
            raise ValueError("Location must be a list of exactly 2 integers [x, y]")
        x, y = loc[0], loc[1]
        desktop.click(loc=loc, button=button, clicks=clicks)
        num_clicks = {0: "Hover", 1: "Single", 2: "Double"}
        return f"{num_clicks.get(clicks)} {button} clicked at ({x},{y})."

    @mcp.tool(
        name="Type",
        description="Types text at specified coordinates [x, y] or passing a UI element's label/id. Set clear=True to clear existing text first, False to append. Set press_enter=True to submit after typing. Set caret_position to 'start' (beginning), 'end' (end), or 'idle' (default). Provide either loc or label.",
        annotations=ToolAnnotations(
            title="Type",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Type-Tool")
    def type_tool(
        text: str,
        loc: list[int] | str | None = None,
        label: int | None = None,
        clear: bool | str = False,
        caret_position: Literal["start", "idle", "end"] = "idle",
        press_enter: bool | str = False,
        ctx: Context = None,
    ) -> str:
        desktop = get_desktop()
        loc = _as_loc(loc)
        if loc is None and label is None:
            raise ValueError("Either loc or label must be provided.")
        if label is not None:
            loc = _resolve_label(desktop, label)
        if len(loc) != 2:
            raise ValueError("Location must be a list of exactly 2 integers [x, y]")
        x, y = loc[0], loc[1]
        desktop.type(
            loc=loc,
            text=text,
            caret_position=caret_position,
            clear=clear,
            press_enter=press_enter,
        )
        return f"Typed {text} at ({x},{y})."

    @mcp.tool(
        name="Scroll",
        description="Scrolls at coordinates [x, y], a UI element's label/id, or current mouse position if loc=None. Type: vertical (default) or horizontal. Direction: up/down for vertical, left/right for horizontal. wheel_times controls amount (1 wheel ≈ 3-5 lines). Use for navigating long content, lists, and web pages.",
        annotations=ToolAnnotations(
            title="Scroll",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Scroll-Tool")
    def scroll_tool(
        loc: list[int] | str | None = None,
        label: int | None = None,
        type: Literal["horizontal", "vertical"] = "vertical",
        direction: Literal["up", "down", "left", "right"] = "down",
        wheel_times: int = 1,
        ctx: Context = None,
    ) -> str:
        desktop = get_desktop()
        loc = _as_loc(loc)
        if label is not None:
            loc = _resolve_label(desktop, label)
        if loc and len(loc) != 2:
            raise ValueError("Location must be a list of exactly 2 integers [x, y]")
        response = desktop.scroll(loc, type, direction, wheel_times)
        if response:
            return response
        return (
            f"Scrolled {type} {direction} by {wheel_times} wheel times"
            + f" at ({loc[0]},{loc[1]})."
            if loc
            else ""
        )

    @mcp.tool(
        name="Move",
        description=(
            "Moves mouse cursor to coordinates [x, y] or passing a UI element's label/id. "
            "Set drag=True to perform a drag-and-drop operation from the current mouse position "
            "to the target coordinates, or provide from_loc=[x, y] to make the drag explicit-start "
            "and atomic in one tool call. Optional duration controls bounded intermediate movement. "
            "Default (drag=False) is a simple cursor move (hover). "
            "Provide either loc or label."
        ),
        annotations=ToolAnnotations(
            title="Move",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Move-Tool")
    def move_tool(
        loc: list[int] | str | None = None,
        label: int | None = None,
        drag: bool | str = False,
        from_loc: list[int] | str | None = None,
        duration: float | int | str | None = None,
        ctx: Context = None,
    ) -> str:
        desktop = get_desktop()
        loc = _as_loc(loc)
        from_loc = _as_loc(from_loc)
        drag = _as_bool(drag, "drag")
        if loc is None and label is None:
            raise ValueError("Either loc or label must be provided.")
        if label is not None:
            loc = _resolve_label(desktop, label)
        if not isinstance(loc, list) or len(loc) != 2:
            raise ValueError("loc must be a list of exactly 2 integers [x, y]")
        if from_loc is not None and (not isinstance(from_loc, list) or len(from_loc) != 2):
            raise ValueError("from_loc must be a list of exactly 2 integers [x, y]")
        has_drag_only_options = any(
            value is not None
            for value in (
                from_loc,
                duration,
            )
        )
        if has_drag_only_options and not drag:
            raise ValueError("from_loc and duration require drag=True")
        if drag:
            loc = _as_point(loc, "loc")
            if from_loc is not None:
                from_loc = _as_point(from_loc, "from_loc")
        x, y = loc[0], loc[1]
        if drag:
            result = desktop.drag(
                loc,
                from_loc=from_loc,
                duration=duration,
            )
            start_x, start_y = result["start"]
            effective_duration = result["duration"]
            if effective_duration is None:
                return f"Dragged from ({start_x},{start_y}) to ({x},{y})."
            return (
                f"Dragged from ({start_x},{start_y}) to ({x},{y}) "
                f"over {effective_duration:.3f} seconds."
            )
        else:
            desktop.move(loc)
            return f"Moved the mouse pointer to ({x},{y})."

    @mcp.tool(
        name="Shortcut",
        description='Executes keyboard shortcuts using key combinations separated by +. Examples: "ctrl+c" (copy), "ctrl+v" (paste), "alt+tab" (switch apps), "win+r" (Run dialog), "win" (Start menu), "ctrl+shift+esc" (Task Manager). Use for quick actions and system commands.',
        annotations=ToolAnnotations(
            title="Shortcut",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Shortcut-Tool")
    def shortcut_tool(shortcut: str, ctx: Context = None):
        get_desktop().shortcut(shortcut)
        return f"Pressed {shortcut}."

    @mcp.tool(
        name="Wait",
        description="Pauses execution for specified duration in seconds. Use when waiting for: applications to launch/load, UI animations to complete, page content to render, dialogs to appear, or between rapid actions. Helps ensure UI is ready before next interaction.",
        annotations=ToolAnnotations(
            title="Wait",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "Wait-Tool")
    def wait_tool(duration: int, ctx: Context = None) -> str:
        time.sleep(duration)
        return f"Waited for {duration} seconds."

    @mcp.tool(
        name="BatchActions",
        description=(
            "Executes a bounded sequence of generic desktop actions in one MCP round trip. "
            "Supported action types: click, move, type, shortcut, scroll, wait, switch_window. "
            "Each item is an object with type plus the same core fields used by the individual tools. "
            "Actions run in order and stop on the first error; at most 25 actions are allowed."
        ),
        annotations=ToolAnnotations(
            title="BatchActions",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "BatchActions-Tool")
    def batch_actions_tool(
        actions: list[_BatchAction],
        stop_on_error: bool | str = True,
        ctx: Context = None,
    ) -> str:
        desktop = get_desktop()
        if isinstance(actions, str):
            actions = json.loads(actions)
        if not isinstance(actions, list) or not actions:
            raise ValueError("actions must be a non-empty list")
        if len(actions) > _MAX_BATCH_ACTIONS:
            raise ValueError(f"actions may contain at most {_MAX_BATCH_ACTIONS} items")
        stop = _as_bool(stop_on_error, "stop_on_error")

        # Validate the complete sequence before the first side effect.
        validated = _BATCH_ADAPTER.validate_python(actions)
        total_wait = sum(
            action.duration for action in validated if isinstance(action, _WaitBatchAction)
        )
        if total_wait > _MAX_BATCH_WAIT_SECONDS:
            raise ValueError(
                f"cumulative wait duration may not exceed {_MAX_BATCH_WAIT_SECONDS:g} seconds"
            )

        prepared: list[tuple[_BatchAction, list[int] | None]] = []
        for action in validated:
            loc = getattr(action, "loc", None)
            label = getattr(action, "label", None)
            if loc is None and label is not None:
                loc = _resolve_label(desktop, label)
            prepared.append((action, loc))

        results: list[dict[str, Any]] = []
        for index, (action, loc) in enumerate(prepared):
            action_type = action.type
            try:
                if isinstance(action, _ClickBatchAction):
                    desktop.click(loc=loc, button=action.button, clicks=action.clicks)
                    detail = f"clicked {action.button} x{action.clicks} at {loc}"
                elif isinstance(action, _MoveBatchAction):
                    if action.drag:
                        desktop.drag(loc, from_loc=action.from_loc, duration=action.duration)
                        detail = f"dragged to {loc}"
                    else:
                        desktop.move(loc)
                        detail = f"moved to {loc}"
                elif isinstance(action, _TypeBatchAction):
                    desktop.type(
                        loc=loc,
                        text=action.text,
                        caret_position=action.caret_position,
                        clear=action.clear,
                        press_enter=action.press_enter,
                    )
                    detail = f"typed {len(action.text)} character(s) at {loc}"
                elif isinstance(action, _ShortcutBatchAction):
                    desktop.shortcut(action.shortcut)
                    detail = f"pressed {action.shortcut}"
                elif isinstance(action, _ScrollBatchAction):
                    scroll_result = desktop.scroll(
                        loc, action.axis, action.direction, action.wheel_times
                    )
                    if isinstance(scroll_result, str) and scroll_result.lower().startswith("error"):
                        raise RuntimeError(scroll_result)
                    detail = f"scrolled {action.axis} {action.direction}"
                elif isinstance(action, _WaitBatchAction):
                    time.sleep(action.duration)
                    detail = f"waited {action.duration} second(s)"
                else:
                    detail, status = desktop.switch_app(action.name)
                    if status != 0:
                        raise RuntimeError(detail)
                results.append({"index": index, "type": action_type, "ok": True, "detail": detail})
            except Exception as exc:
                failure = {"index": index, "type": action_type, "ok": False, "error": str(exc)}
                results.append(failure)
                if stop:
                    raise RuntimeError(
                        f"BatchActions failed at action {index} ({action_type}): {exc}; "
                        f"{len(results) - 1} action(s) completed"
                    ) from exc

        return json.dumps(
            {
                "ok": all(result["ok"] for result in results),
                "completed": sum(1 for result in results if result["ok"]),
                "requested": len(actions),
                "results": results,
            },
            ensure_ascii=False,
        )

    @mcp.tool(
        name="WaitFor",
        description=(
            "Waits until a UI condition is satisfied, polling the Windows accessibility tree "
            "inside the tool to avoid repeated Snapshot calls. Conditions: text_exists, "
            "active_window, element_exists, element_enabled, focused_element. Provide text "
            "and/or window_name depending on the condition. Set use_dom=True for browser DOM text."
        ),
        annotations=ToolAnnotations(
            title="WaitFor",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    @with_analytics(get_analytics(), "WaitFor-Tool")
    def wait_for_tool(
        condition: str,
        text: str | None = None,
        window_name: str | None = None,
        timeout: float = 10.0,
        interval: float = 0.25,
        use_dom: bool | str = False,
        ctx: Context = None,
    ) -> str:
        normalized = _validate_wait_for_args(
            condition=condition,
            text=text,
            window_name=window_name,
            timeout=timeout,
            interval=interval,
        )
        desktop = get_desktop()
        use_dom_bool = _as_bool(use_dom, "use_dom")
        started_at = time.monotonic()
        deadline = started_at + timeout
        attempts = 0
        last_detail = "condition was not evaluated"

        while True:
            attempts += 1
            # Active-window waits are common after App.switch, launch, and OS
            # shortcuts. Walking every top-level window and up to 500 UIA nodes
            # on each poll made this simple title check take tens of seconds on
            # busy multi-monitor desktops. Query GetForegroundWindow directly;
            # retain the full tree path for conditions that actually need it.
            if normalized == "active_window" and not use_dom_bool:
                fast_getter = getattr(desktop, "get_active_window", None)
                if callable(fast_getter):
                    active_window = fast_getter(windows=[])
                    active_name = active_window.name if active_window else ""
                    expected = window_name or text
                    matched = _text_matches(active_name, expected)
                    last_detail = (
                        f"active window matched {active_name!r}"
                        if matched
                        else f"active window was {active_name!r}"
                    )
                else:
                    desktop_state = desktop.get_state(
                        use_vision=False,
                        use_dom=False,
                        use_ui_tree=True,
                        use_annotation=False,
                    )
                    matched, last_detail = _matches_wait_condition(
                        desktop_state=desktop_state,
                        condition=normalized,
                        text=text,
                        window_name=window_name,
                    )
            else:
                desktop_state = desktop.get_state(
                    use_vision=False,
                    use_dom=use_dom_bool,
                    use_ui_tree=True,
                    use_annotation=False,
                )
                matched, last_detail = _matches_wait_condition(
                    desktop_state=desktop_state,
                    condition=normalized,
                    text=text,
                    window_name=window_name,
                )
            if matched:
                elapsed = time.monotonic() - started_at
                return (
                    f"WaitFor condition '{normalized}' satisfied after "
                    f"{elapsed:.2f}s and {attempts} attempt(s): {last_detail}."
                )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Timed out after {timeout:.2f}s waiting for '{normalized}': {last_detail}."
                )
            time.sleep(min(interval, remaining))
