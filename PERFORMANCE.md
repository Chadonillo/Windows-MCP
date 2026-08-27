# Generic desktop-control performance extensions

This fork preserves CursorTouch Windows-MCP while adding application-agnostic
performance primitives for busy multi-monitor Windows desktops.

The changes are generic. They do not contain Spotify-, Gmail-, Smallboi-, or
other application-specific automation.

## Added behavior

### Constant-time active-window waits

`WaitFor(condition="active_window")` queries the foreground-window API directly
rather than walking the full accessibility tree on every poll. Text and element
conditions retain their accessibility-tree behavior.

### Fast top-level application switching

`App(mode="switch")` resolves top-level window titles and handles without a
full child-control traversal.

### Scoped snapshots

`Snapshot` accepts:

- `focused_only=true`
- `window_name="..."`
- `max_elements=N` as an explicit per-capture tree-budget override

Scoped snapshots traverse only one top-level window and crop visual capture to
that window. Omitting scope retains the original full-desktop behavior.

Correctness rules:

- `focused_only` and `window_name` are mutually exclusive.
- Window scope cannot be combined with `display` or `region`.
- A named background target is reported separately as `Scoped Window`; the
  actual foreground window remains `Focused Window`.
- Ambiguous, minimized, hidden, empty-bounds, and invalid DOM targets fail
  explicitly.
- The existing environment-controlled element budget remains the default unless
  `max_elements` is supplied.

### BatchActions

`BatchActions` executes up to 25 ordered generic input/window actions in one MCP
round trip:

- click
- move/drag
- type
- shortcut
- scroll
- wait
- switch_window

The complete action sequence is validated before the first side effect through
a strict discriminated Pydantic schema. Unknown fields and invalid parameter
combinations are rejected. Cumulative waits are capped at 30 seconds. With
`stop_on_error=true`, runtime failures surface as MCP errors.

## Representative benchmark

Measured on Windows 11 with two 1920×1080 displays plus one 1440×2560 portrait
display and many open desktop applications:

| Operation | Before | After |
|---|---:|---:|
| Active-window wait | 6–30+ s | 74–113 ms |
| First application switch | could trigger >60 s tree walk | 1.0–1.9 s |
| Focused complex-app snapshot | >60 s timeout | 8.7–9.8 s |
| Focused small-dialog snapshot | full desktop walk | 242 ms |
| Three ordered actions | three MCP/model turns | ~1.18 s total |
| Screenshot, one 1080p display | ~854 ms | unchanged |
| Screenshot, all three displays | ~1.45 s | unchanged |

Raw screenshot cost scales with monitor pixels, but the dominant prior latency
was global Windows UI Automation traversal.

## Validation

- Focused/hardening tests: 53 passed.
- Broad suite excluding scheduled-task mutation test files: 509 passed.
- An earlier complete suite passed 513 tests before the final additive safety
  tests.
- Live MCP checks verified the discriminated schema and `isError=true` for
  validation and runtime failures.

Scheduled-task installer tests should be run in an isolated Windows test
machine because those tests intentionally exercise task creation/removal.
