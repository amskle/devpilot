# DevPilot Web Design System

## Product posture

DevPilot is a developer workbench for directing an engineering agent and reviewing its evidence. The primary interaction is a conversation, but ordinary messages never imply approval, cancellation, rollback, restore, or replanning. Those remain explicit controls.

The interface serves developers and technical leads who need to move quickly without losing the execution plan, revision boundary, risk decision, verification result, or durable event history.

## Structural pattern

- Hallmark genre: `modern-minimal`
- Macrostructure: `Workbench`
- Theme family: `Cobalt`, with paired light and dark modes
- Default route: a new-task conversation
- Persistent left region: task history, search, and a collapsed runtime overview
- Central region: user/agent conversation with auditable execution evidence
- Bottom region: sticky composer; ordinary message and formal ChangeRequest are separate actions
- Top-right utilities: runtime state, theme toggle, and access credentials
- Closing status: a single-line runtime and safety statement, not a sitemap footer

The layout is left-biased. The history rail is stable on desktop and becomes a dismissible sheet below the desktop breakpoint. The conversation column owns the remaining width and never becomes a centred full-viewport hero.

## Typography

- Display and product mark: Space Grotesk, weights 500 and 700
- Body and controls: IBM Plex Sans, weights 400, 500, and 600
- IDs, revisions, hashes, diffs, and numeric telemetry: JetBrains Mono, weights 400 and 600
- Body text starts at 16 px with 1.55 line height
- Headings use a 1.25 ratio and tight tracking; no italic emphasis and no decorative section eyebrows
- Clickable labels remain one line at all supported widths

## Colour

All application colours are defined as OKLCH tokens in `frontend/vue3/src/tokens.css`. No component may introduce an inline colour value.

Light mode uses cool near-white paper, blue-tinted neutral surfaces, deep navy ink, and cobalt as a small active/focus signal. Dark mode keeps the same hue family with low-glare navy paper, lighter raised surfaces, near-white ink, and a slightly lighter, less chromatic cobalt.

Accent use is limited to focus, selection, active navigation, compact indicators, and links. Large surfaces and primary action buttons use ink/paper contrast instead of saturated accent fills. Status always combines colour with text or shape.

## Spacing and geometry

- 4 px base grid with semantic spacing tokens from 2 px to 96 px
- 44 px minimum interactive height; 48 px for coarse pointers
- Inputs and adjacent buttons share the same base height
- Panels use one containment boundary; nested card-on-card decoration is prohibited
- Rules and surface lightness create depth; shadows are limited to overlays in light mode
- Border width stays constant across input states
- Six named z-index levels cover raised, sticky, modal, toast, and tooltip surfaces

## Motion

Workbench motion is limited to three primitives:

1. Theme surface crossfade.
2. Sidebar sheet entrance and dismissal.
3. Button press and composer submission feedback.

Durations use the named 120 ms, 220 ms, and 420 ms buckets with exponential easing. Focus rings appear immediately. Reduced-motion mode removes spatial movement and caps transitions at 150 ms.

## Conversation contract

- A user task request appears as a user message.
- Agent output contains concise status and links to evidence, never hidden chain-of-thought.
- Plan, Timeline, Diff, Verification, Budget, Approval, Intervention, and Recovery are evidence/control surfaces inside the conversation workspace.
- Ordinary task messages are persisted as independent message events and do not mutate the plan.
- Formal ChangeRequest is labelled separately, warns when it invalidates a pending patch, and requires confirmation at the existing safety boundary.
- No success toast is emitted when the new state is already visible.

## Responsive contract

Verify at 320, 375, 414, 768, 960, and 1440 CSS pixels.

- `html` and `body` use `overflow-x: clip`.
- The desktop sidebar becomes an off-canvas sheet under 60 rem.
- Composer actions reflow while every action label stays on one line.
- Evidence tabs scroll horizontally without wrapping.
- Data grids collapse to a single column; diff and event streams scroll inside their own content region.
- Touch targets remain at least 44 px and every hover treatment has focus/tap parity.

## Accessibility and safety

- Visible labels remain above all inputs; placeholders only provide example format.
- Every interactive element has visible `:focus-visible` treatment.
- Errors name the failed action and provide a retry or correction.
- Live connection state uses text plus a dot; approval and verification states use text plus shape.
- Theme preference is persisted locally. A new browser starts in light mode so the workbench never defaults back to the former dark-only presentation.
- Access tokens remain in `sessionStorage`; event payloads, messages, and diffs render as text.

## Exports

### CSS source of truth

The complete light/dark token set is maintained in `frontend/vue3/src/tokens.css` and loaded before `style.css` from the Vue entry point. Components consume semantic names only.

```css
@import "./tokens.css";

.surface {
  background-color: var(--color-surface);
  color: var(--color-ink);
  border-color: var(--color-rule);
  font-family: var(--font-body);
}
```

### Tailwind v4

```css
@theme {
  --color-paper: oklch(98% 0.008 250);
  --color-paper-subtle: oklch(96% 0.012 250);
  --color-surface: oklch(99% 0.006 250);
  --color-rule: oklch(86% 0.018 250);
  --color-rule-strong: oklch(60% 0.035 250);
  --color-neutral: oklch(48% 0.025 250);
  --color-muted: oklch(39% 0.03 250);
  --color-ink-soft: oklch(31% 0.034 250);
  --color-ink: oklch(20% 0.035 250);
  --color-accent: oklch(46% 0.16 255);
  --color-accent-ink: oklch(98% 0.008 250);
  --color-focus: oklch(48% 0.2 255);

  --font-display: "Space Grotesk", sans-serif;
  --font-body: "IBM Plex Sans", sans-serif;
  --font-mono: "JetBrains Mono", monospace;

  --spacing-3xs: 0.125rem;
  --spacing-2xs: 0.25rem;
  --spacing-xs: 0.5rem;
  --spacing-sm: 0.75rem;
  --spacing-md: 1rem;
  --spacing-lg: 1.5rem;
  --spacing-xl: 2.5rem;
  --spacing-2xl: 4rem;

  --radius-sm: 0.375rem;
  --radius-md: 0.625rem;
  --radius-lg: 0.875rem;
  --radius-pill: 999rem;
  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --ease-in: cubic-bezier(0.7, 0, 0.84, 0);
  --ease-in-out: cubic-bezier(0.65, 0, 0.35, 1);
}
```

### DTCG tokens.json

```json
{
  "$schema": "https://design-tokens.github.io/community-group/format/",
  "color": {
    "paper": { "$value": "oklch(98% 0.008 250)", "$type": "color" },
    "paper-subtle": { "$value": "oklch(96% 0.012 250)", "$type": "color" },
    "surface": { "$value": "oklch(99% 0.006 250)", "$type": "color" },
    "rule": { "$value": "oklch(86% 0.018 250)", "$type": "color" },
    "rule-strong": { "$value": "oklch(60% 0.035 250)", "$type": "color" },
    "muted": { "$value": "oklch(39% 0.03 250)", "$type": "color" },
    "ink": { "$value": "oklch(20% 0.035 250)", "$type": "color" },
    "accent": { "$value": "oklch(46% 0.16 255)", "$type": "color" },
    "accent-ink": { "$value": "oklch(98% 0.008 250)", "$type": "color" },
    "focus": { "$value": "oklch(48% 0.2 255)", "$type": "color" }
  },
  "font": {
    "display": { "$value": "Space Grotesk, sans-serif", "$type": "fontFamily" },
    "body": { "$value": "IBM Plex Sans, sans-serif", "$type": "fontFamily" },
    "mono": { "$value": "JetBrains Mono, monospace", "$type": "fontFamily" }
  },
  "space": {
    "2xs": { "$value": "0.25rem", "$type": "dimension" },
    "xs": { "$value": "0.5rem", "$type": "dimension" },
    "sm": { "$value": "0.75rem", "$type": "dimension" },
    "md": { "$value": "1rem", "$type": "dimension" },
    "lg": { "$value": "1.5rem", "$type": "dimension" },
    "xl": { "$value": "2.5rem", "$type": "dimension" }
  },
  "duration": {
    "micro": { "$value": "120ms", "$type": "duration" },
    "short": { "$value": "220ms", "$type": "duration" },
    "long": { "$value": "420ms", "$type": "duration" }
  }
}
```

### shadcn/ui variables

```css
:root {
  --background: 98% 0.008 250;
  --foreground: 20% 0.035 250;
  --card: 99% 0.006 250;
  --card-foreground: 20% 0.035 250;
  --popover: 100% 0.005 250;
  --popover-foreground: 20% 0.035 250;
  --primary: 46% 0.16 255;
  --primary-foreground: 98% 0.008 250;
  --secondary: 96% 0.012 250;
  --secondary-foreground: 31% 0.034 250;
  --muted: 86% 0.018 250;
  --muted-foreground: 39% 0.03 250;
  --accent: 92% 0.045 255;
  --accent-foreground: 20% 0.035 250;
  --destructive: 45% 0.17 25;
  --destructive-foreground: 98% 0.008 250;
  --border: 86% 0.018 250;
  --input: 60% 0.035 250;
  --ring: 48% 0.2 255;
  --radius: 0.625rem;
}

.dark {
  --background: 15% 0.018 250;
  --foreground: 94% 0.012 250;
  --card: 19% 0.022 250;
  --card-foreground: 94% 0.012 250;
  --popover: 22% 0.025 250;
  --popover-foreground: 94% 0.012 250;
  --primary: 70% 0.13 255;
  --primary-foreground: 15% 0.018 250;
  --secondary: 17% 0.021 250;
  --secondary-foreground: 84% 0.018 250;
  --muted: 29% 0.03 250;
  --muted-foreground: 73% 0.025 250;
  --accent: 25% 0.055 255;
  --accent-foreground: 94% 0.012 250;
  --destructive: 73% 0.14 25;
  --destructive-foreground: 15% 0.018 250;
  --border: 29% 0.03 250;
  --input: 55% 0.04 250;
  --ring: 77% 0.14 255;
}
```
