# Inferia Brand & Design System

> Universal design language for all Inferia products, surfaces, and communications. Everything built under the Inferia name follows these rules. No exceptions.

## Brand Identity

Inferia is the operating system enterprises install to own and run AI in house. The brand feels like quiet confidence. Not loud, not flashy, not trying to prove anything. It speaks clearly because it has something real to say.

**Personality:** A knowledgeable colleague who builds with care. Technical but approachable. Warm but not soft. Direct but not cold.

## Color System

### Light theme (default)

| Token | Hex | Usage |
|---|---|---|
| `--bg-primary` | `#FAFAF8` | Page backgrounds, main canvas |
| `--bg-secondary` | `#F2F0EC` | Cards, sections with depth, input backgrounds |
| `--text-primary` | `#1A1A1A` | Headlines, primary content, logo |
| `--text-secondary` | `#5C5C5C` | Descriptions, supporting text |
| `--text-muted` | `#9A9A9A` | Captions, metadata, timestamps |
| `--accent-warm` | `#E8603C` | CTAs, highlights, interactive elements (ember orange) |
| `--accent-soft` | `#F0E6D3` | Hover backgrounds, gentle highlights (parchment) |
| `--border` | `#E5E2DC` | Dividers, card borders, input borders |
| `--success` | `#3D7A5F` | Positive states (muted forest green) |
| `--white` | `#FFFFFF` | Contrast elements |

### Dark theme

| Token | Hex | Usage |
|---|---|---|
| `--bg-primary` | `#0a0a0a` | Page background |
| `--bg-secondary` | `#141414` | Cards, sidebar, elevated surfaces |
| `--bg-tertiary` | `#1A1A1A` | Input backgrounds, code blocks |
| `--text-primary` | `#E8E8E8` | Headlines |
| `--text-secondary` | `#A0A0A0` | Body text |
| `--text-muted` | `#666666` | Metadata |
| `--accent` | `#E8603C` | Same ember accent |
| `--border` | `#2A2A2A` | Borders, dividers |

**Rule:** The ember accent `#E8603C` is the unifying brand color across all themes and products. It appears sparingly: CTAs, key highlights, the occasional detail that draws the eye. Never dominant. Always intentional.

## Typography

| Role | Font | Weight | Usage |
|---|---|---|---|
| Headlines | Inter | 700/800 | Page titles, section headers, hero text |
| Body | Inter | 400/500 | Paragraphs, descriptions, UI labels |
| Code / Data | JetBrains Mono | 400/500 | Code blocks, terminal, metrics, monospace content |

Two fonts. No more. Inter is the voice. JetBrains Mono is for technical contexts only.

**Type scale:** 1.25 ratio. Base 16px mobile, 18px desktop. Headlines speak clearly, never shout.

## Spacing & Layout

- 8px base grid. All spacing in multiples of 8.
- Max content width: 1200px.
- Section padding: 80-120px vertical (desktop), 48-64px (mobile).
- Border radius: 8px for cards/containers. 4px for buttons/inputs. Not fully rounded, not knife sharp.
- Shadows: Subtle and warm. `0 1px 3px rgba(0,0,0,0.06)` for cards. No heavy drop shadows.

## Component Language

### Buttons

- **Primary:** `--accent-warm` background, white text. Sharp corners (4px radius max).
- **Secondary:** Transparent background, `--border` border, `--text-primary` text.
- **Ghost:** No border, text only. Underline on hover.
- All buttons: 12-16px padding vertical, 20-32px horizontal. Inter 500 weight, 14px.

### Cards

- Background: `--bg-secondary` on light, `--bg-secondary` on dark.
- Border: 1px `--border`.
- Radius: 8px.
- Hover: subtle border darken or lift shadow.
- No rounded corners beyond 8px.

### Inputs

- Border: 1px `--border`. Radius: 4px.
- Focus: border transitions to `--accent-warm`.
- Background: `--bg-primary` or `--bg-secondary`.
- 16px font size on mobile (prevents iOS zoom).

### Tables

- Clean grid. Alternating row tint optional.
- Header: `--text-muted`, uppercase, smaller, JetBrains Mono.
- Cells: `--text-primary`, regular weight.
- Border: bottom only, `--border`.

### Code Blocks

- Background: `--bg-tertiary` (or dark bg in light theme: `#1A1A1A`).
- Font: JetBrains Mono 13-14px.
- Radius: 8px.
- Subtle border.
- Copy button top-right.

## Copy Rules

- **No hyphens.** Write "self hosted" not "self-hosted", "open weight" not "open-weight", "real time" not "real-time".
- **Natural language.** Copy reads like a warm conversation. Not marketing speak, not corporate jargon.
- **Direct.** Say what it does. "Run AI on your infrastructure" not "Leverage next-gen AI capabilities."
- **Warm but not fluffy.** Confident, not arrogant. Technical, not intimidating.
- **No stock phrases.** No "revolutionizing", no "leveraging", no "transforming", no "journey."

## Logo Usage

- Light background: black logo (`#1A1A1A`)
- Dark background: white logo (`#FFFFFF`)
- Interactive/accent: ember (`#E8603C`)
- Minimum clear space: half the icon width
- Inline SVG preferred in web contexts
- Never stretch, rotate, add effects

**Logo variants available:**
- Icon mark (standalone brain helmet) — black, white, accent, on-dark, on-light
- Full lockup (icon + INFERIA text) — black, white
- Wordmark only (text, no icon) — black, white
- Watermark (8% opacity for backgrounds)
- Favicon (16x16)

## Imagery

- **No stock photos. Ever.**
- Custom SVG illustrations in minimal line art style with brand palette.
- Diagrams are first class content. Clean, labeled, branded.
- Icon mark as subtle watermark or section accent where appropriate.
- Photography (if used): Real photos only. Natural light. No forced poses.

## Motion

- Gentle fade ins on scroll. Elements breathe into view.
- Hover states feel responsive but not jumpy.
- No gratuitous animations, no flashy transitions.
- Movement is purposeful. If it doesn't add clarity, remove it.

## Responsive

| Breakpoint | Target |
|---|---|
| `< 640px` | Mobile phones |
| `640-1024px` | Tablets / small laptops |
| `1024-1440px` | Standard desktops |
| `> 1440px` | Large displays (content stays centered) |

**Rules:**
- Mobile first, always.
- Text never below 14px.
- CTAs always thumb reachable on mobile.
- No horizontal scrolling.
- 16px inputs on mobile (prevents iOS zoom).

## Applies To

This design direction governs:

- Website (inferia.ai)
- Documentation site (docs.inferia.ai)
- Product UIs (InferiaLLM dashboard, InferiaGate console, admin panels, inferia-auth-ui)
- Marketing materials (social posts, decks, PDFs, one pagers)
- Email templates (transactional, newsletters)
- Internal tools (plan-viewer uses ember accent to match the rest of the brand)

All customer facing surfaces use the ember accent `#E8603C`.

## Banned vs Preferred Copy

| Banned | Preferred |
|---|---|
| Authentication failed | That email and password don't match |
| Invalid credentials | We don't recognize this account |
| Authorization required | You need to sign in first |
| Authorize | Sign in |
| Login | Sign in |
| Logout | Sign out |
| Real-time updates | Real time updates |
| Self-hosted | Self hosted |
| Open-weight | Open weight |
| Leveraging X | Backed by X |
| Revolutionizing | (removed entirely) |
| Transforming | (removed entirely) |
| Journey | (removed entirely) |
