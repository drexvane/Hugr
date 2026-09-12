# Cipher IDE — Frontend Design

## Brief

A desktop IDE (Tauri + React) for a data-to-insights agent. A user opens a folder
of raw, messy data as a **workspace**, the same way they'd open a repo in VS Code.
An agent panel reads the data, runs it through cleaning and validation, produces
charts, and answers questions — but only by filling in a validated plan against a
metric registry, never by writing raw SQL. Every number the agent shows has to be
traceable back to a real column in a real snapshot.

Audience: data analysts and engineers, not consumers. They already live in
terminals, diffs, and log output. The interface should feel like an instrument
panel for a pipeline, not a dashboard selling itself.

## Design plan

### Color

The core metaphor is a **control room reading live telemetry**, not a SaaS
product. Color carries meaning first, mood second — it mirrors Cipher's own
severity model (critical → warning → info) and its published/unpublished
snapshot states, so the palette is built around status, not decoration.

| Token | Hex | Role |
|---|---|---|
| `ink-950` | `#0E1113` | App chrome, panel backgrounds |
| `ink-800` | `#1B2024` | Elevated surfaces (cards, panels, active tab) |
| `line-700` | `#2C3338` | Borders, dividers, inactive rules |
| `paper-100` | `#E7E4DD` | Primary text on dark chrome (warm off-white, not pure white) |
| `signal-amber` | `#C97A2E` | Validation warnings, pending/unpublished state |
| `signal-teal` | `#3E8E82` | Passed validation, published snapshot, "trustworthy" state |
| `signal-red` | `#B4453A` | Blocked pipeline, failed rule, critical alert |

Only one accent is "live" at a time in any given view — a chart is teal (clean),
amber (flagged), or red (blocked); it doesn't carry all three as decoration.
No gradients. No soft drop shadows. Surfaces are separated by a 1px line or a
flat elevation step, the way panels separate in a terminal multiplexer.

### Type

Two families, both chosen because they were designed for dense technical
interfaces rather than marketing pages:

- **IBM Plex Sans** — UI chrome, panel labels, agent prose, body text.
- **IBM Plex Mono** — anything that is literally data: file paths, snapshot
  timestamps, the agent's plan JSON, column names, row counts. Monospace here
  is functional (columns need to align, hashes need to be scannable), not a
  decorative label face.

Type scale stays small and dense — this is a multi-pane tool, not a landing
page. Body/UI text 13px, panel headers 12px medium (not all-caps — see
Restraint below), data/code 12.5px mono with 1.5 line-height for scanability.
Line length inside the agent panel capped around 68 characters so prose stays
readable in a narrow sidebar.

### Layout

Four-pane IDE shape, but each pane maps to a real stage of the pipeline rather
than being generic chrome:

```
┌─────────────┬────────────────────────────┬───────────────────┐
│  WORKSPACE   │      DATA / CHART CANVAS   │   AGENT           │
│              │                             │                   │
│  dataset/    │  [chart or table renders    │  > clean this     │
│   raw.csv    │   here, teal/amber/red      │    folder         │
│  data/       │   framed by validation      │                   │
│   clean/     │   state]                    │  Plan:            │
│   versions/  │                             │  { metrics: [...] │
│  reports/    │                             │    window: ...}   │
│              │                             │                   │
├─────────────┴────────────────────────────┴───────────────────┤
│  PIPELINE STRIP: clean ✓  validate ✓  snapshot ✓  dict ✓  monitor ✓ │
└─────────────────────────────────────────────────────────────────┘
```

- **Left — Workspace.** File tree of the opened folder. Left-aligned, monospace
  paths, status glyphs next to files that have a clean/rejected counterpart
  (e.g. a small teal dot beside a file once it has a published snapshot).
- **Center — Canvas.** Where charts, tables, or a raw file preview render.
  Framed with a thin colored rule (teal/amber/red) reflecting the validation
  state of the data behind what's shown — the frame is the trust signal, not a
  badge stapled on top.
- **Right — Agent.** Chat above, but the model's **plan is always visible**
  below the reply, in mono, collapsed by default with a one-line summary
  (`metrics: revenue, profit · by: region · window: 2017`) and expandable —
  because an agent that hides its plan is exactly what Cipher's own design
  argues against.
- **Bottom — Pipeline strip.** Not a generic status bar. It's the same five
  stages Cipher's CLI already prints (`clean → validate → snapshot → dictionary
  → monitor`), shown as a persistent horizontal ledger so the user always knows
  which stage they're looking at output from, and whether the run behind it
  actually published.

Alignment is left-throughout; nothing centers. This is a working tool read
top-to-bottom, left-to-right, the way a file tree or a log is read.

### Principles

1. **The frame is the trust signal.** Any number, chart, or plan on screen
   carries its validation state as a border/rule color, not a separate icon
   system layered on top. If it's not clean, it doesn't get to look clean.
2. **Nothing the agent did is hidden.** The plan behind an answer, the stage
   behind a chart, the snapshot behind a number — one click away, never
   buried. This is a direct extension of Cipher's own "`:plan` prints it" and
   "every figure is matched against the frame it was shown" philosophy.
3. **One motion moment, not many.** When a pipeline stage completes, its chip
   in the bottom strip flips state with a brief (150ms) settle — that's the
   one animated beat in the interface. File-tree expansion, panel resize, and
   hover states are instant. No fade-slide-up entrances anywhere.
4. **Status color is load-bearing, not decorative.** Amber and red are never
   used for anything except an actual warning or failure state. If a future
   feature needs a highlight color for something unrelated to validation, it
   should not be pulled from this set.

## What this deliberately avoids

- No warm-cream-and-terracotta or near-black-with-neon-accent defaults — the
  palette is a status system pulled from Cipher's own severity model instead.
- No identical rounded SaaS cards with uniform shadows — panels are flat,
  separated by rules, at different elevation steps.
- No tracked-out ALL-CAPS eyebrows, no middle-dot metadata strings, no
  decorative monospace on non-data labels, no arrows appended to buttons.
- No numbered-step markers anywhere the content isn't actually sequential
  (the pipeline strip is numbered implicitly by being an actual sequence —
  that's the one place ordering is real).

## Skills to invoke in Claude Code

Going by name only (I can't see what's inside each), these are the installed
skills worth pulling in when this brief goes to build:

- **`design-taste-frontend`** — the most directly relevant one; use it to
  keep the build honest against this brief rather than drifting toward
  generic IDE/SaaS defaults as components get built out.
- **`ui-ux-pro-max`** — for component-level UX decisions this brief doesn't
  cover in detail (interaction states, empty states, focus handling across
  the four panes).
- **`web-perf`** — worth a pass once the canvas is rendering real charts and
  large file trees, since a data-heavy workspace can get slow fast (Cipher's
  own dashboard had to fix a 525ms funnel-view regression for exactly this
  reason).
- **`higgsfield-websites`** — only relevant if any part of this ships as a
  marketing/landing page rather than the app shell itself; skip it for the
  in-app IDE views.

`find-skills`, `impeccable`, and `ponytail` aren't self-explanatory from the
name alone — worth a quick look at what each actually does before deciding
if they apply here.