# Cursor usage

A toolbar button labeled Cursor usage opens one dashboard tab. The page is a narrow column of cards on the theme canvas: how much of the Cursor allowance is used, when it resets, and whether the current pace holds.

![Cursor usage](preview.png)

## What you see

- **Toolbar button** (128px, in the tab strip, chrome background, 12px muted label "Cursor usage"). Hover uses the theme hover color.
- **Dashboard** (scrollable canvas, column max width 720, padding 24, or 16 when narrower than 620). Cards are surface, 1px border, radius 8, padding 16. Spacing is 4, 8, 12, 16, 24. Every color comes from the Herdr theme.
- **Header.** "Cursor usage" at 20px semibold. On the right, dim status text ("Updated just now", "Refreshing…", or the error in the blocked color) and an outline Refresh button, 30px tall, radius 6.
- **Hero card.** Uppercase 9.5px label: `INCLUDED THIS WEEK`, `INCLUDED THIS MONTH`, `INCLUDED USAGE`, `SPEND THIS CYCLE`, `ON-DEMAND USAGE`, or `INCLUDED REQUESTS`. The value is 28px: `20%`, `$82`, or `120 / 500`. Under it, one 12.5px muted sentence such as `20% used · 80% left · $16 left`. A 6px rounded track in the border color, filled with foreground. At a projected 80% or above, the value and the fill use the blocked color.
- **Reset and pace,** inside the hero, 11.5px. Reset is dim (`Resets Tue, Nov 18, 4:00 PM · 3d 12h`). The pace sentence is foreground, or blocked when this pace hits the limit before reset.
- **Stat card.** Cells labeled `LEFT`, `PACE`, and `ROOM`. Empty cells are omitted. Pace is `under`, `on pace`, `ahead`, or `used up`. Room is `23% per day` (or per hour, or per minute).
- **Detail card,** only when there is something to show. Cells for `AUTO`, `API`, and `TOKENS`. A dim line under them: `Last commit 80% AI-generated`.
- **Activity card.** Label `ACTIVITY · 14 DAYS`. Up to fourteen bars, 4px wide, gap 4, height scaled to that window, drawn in foreground from the bottom of a 48px row.
- **Account,** dim 11.5px under the cards: `Premium · ada@example.com · On this Mac since Jan 2026`.
- **Empty and error.** No data yet: one card, "Loading Cursor usage" or "Cursor usage is unavailable", with a primary Retry button on the error.

## How it behaves

- The toolbar click focuses an existing Cursor usage pane in the active space, or opens `tab.new` with name "Cursor usage" and mod `cursor-usage`.
- The dashboard reads on show, then every 5 minutes, and when Refresh or Retry is clicked. A read already in flight is left alone.
- One meter, chosen the same way as the cursor-usage menu bar:
  - Included-usage plans: `planUsage.totalPercentUsed`, else `autoPercentUsed`.
  - Team or usage-based spend: dollars for the signed-in email from `get-team-spend`.
  - Otherwise included requests from `GET /api/usage` (`gpt-4` is the aggregate).
- The bar width is the percent used now. The blocked color uses the projected end-of-cycle percent once 10% of the window has passed; before that it uses the percent used now.
- A failed refresh keeps the last cards and puts the message in the header. The first failure, with nothing to show, replaces the hero with the error card.
- No agent status colors. The mod does not depend on an agent running.

## Built from

- One mod, `mods/cursor-usage`. `mod.json` is `{ "place": "none", "title": "Cursor usage" }`. The same mod draws the toolbar when `props.mode` is `"toolbar"`.
- `herdr`: `appHome`, `exec`, `panes`, `props`, `run`, `settings`, `spaces`, `theme`. Commands: `tab.new`, `pane.focus`, `pane.zoom`.
- Program: `python3 mods/cursor-usage/cursor_usage.py`, timeout 90 seconds. If `python3` cannot be started, the same script is run through the login shell. The script runs `sqlite3` (via Python), `security`, and `curl`.
- Install: `python3 scripts/install.py` copies the mod and clones the current layout. `python3 scripts/uninstall.py` switches back and deletes that clone.

## Watch out

- macOS only. The script expects Cursor's `state.vscdb` and the Keychain item **Cursor Safe Storage**. The first live fetch can prompt. Allowing it always lets `/usr/bin/security` read that item later.
- The mod calls `https://www.cursor.com`, runs programs, and reads files outside its folder (the Cursor database and the Keychain). It does not print the token. The cookie jar is mode `0600` and is deleted when the request finishes.
- `"place": "none"` means Herdr will not show it until a layout adds the toolbar block. Do not edit a layout owned by another installer. Insights records a hash of `office-insights.json`; changing that file makes Insights uninstall refuse it.
- Shipped layouts are read-only. The installer clones the layout that is on screen and edits only the clone.
- `herdr-app check` renders the loading card. It does not call Cursor.

## Done when

- `python3 -m unittest tests/cursor_usage_test.py tests/layout_test.py` passes.
- `herdr-app check mods/cursor-usage` passes.
- `python3 scripts/install.py` leaves the previous layout file byte-for-byte unchanged and shows the button on a new layout.
- Opening Cursor usage shows the hero card, and Refresh runs again.
- `python3 scripts/uninstall.py` returns to the previous layout and removes `mods/cursor-usage`. Installing a second time works.
