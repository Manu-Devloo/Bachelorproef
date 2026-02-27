# UI Design Direction

## 1) Design direction summary

- Aesthetic: **Industrial Command Deck**
- Purpose: operational control interface for challenge lifecycle actions
- Differentiation anchor: atmospheric control-room look (teal/orange glow + translucent panels) rather than default SaaS cards

DFII score:

- Aesthetic Impact: 4
- Context Fit: 5
- Implementation Feasibility: 5
- Performance Safety: 4
- Consistency Risk: 2
- **DFII = 16 - 2 = 14** (strong)

## 2) Design system snapshot

Typography:

- Display: `Space Grotesk` (headings and control titles)
- Body: `IBM Plex Sans` (inputs, tables, logs)

Color variables:

- Dominant: deep blue-green background (`--bg-ink`)
- Accent: mint-teal (`--accent`)
- Secondary accent: warm orange (`--accent-2`)
- Text: high-contrast light neutrals (`--text`, `--text-soft`)

Spacing rhythm:

- Panel-driven layout with 0.45rem / 0.8rem / 1.1rem / 1.2rem steps

Motion philosophy:

- Minimal but meaningful
- Single entrance sequence (`panel-reveal`) with stagger per section
- Lightweight hover lift on action buttons

## 3) Implementation notes

- Implemented in:
  - `app/templates/index.html`
  - `app/static/styles.css`
  - `app/static/app.js`
- Uses semantic sections, accessible form labels, and clear state tags.

## 4) Differentiation callout

This avoids generic UI by using a control-room visual language (layered translucent panels, asymmetric glow fields, and explicit operational typography) instead of default white SaaS cards and neutral palettes.
