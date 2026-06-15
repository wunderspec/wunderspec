# Wunderspec logo design package

Selected concept: **Version 5 — State-W Constellation**.

This package contains clean SVG source files and PNG exports for GitHub, README headers, and website use. The SVG files keep text editable and declare this font stack:

```css
font-family: 'JetBrains Mono', 'SFMono-Regular', 'DejaVu Sans Mono', 'Liberation Mono', monospace;
```

The JetBrains Mono font file is **not included** in this package. Install it locally or load it on the website for exact rendering. The PNG exports were rendered from the SVGs in this environment and may use a local monospace fallback if JetBrains Mono is unavailable.

## Palette

| Token | Hex | Use |
|---|---:|---|
| Deep Navy | `#020A17` | main background |
| Ink Navy | `#061426` | subtle glow / panel depth |
| Star White | `#F8FBFF` | nodes, wordmark, primary transitions |
| Pale Cyan | `#7BE8F4` | temporal arc / optional active state |
| Amber | `#FFB22E` | active transition, tagline |
| Muted Blue | `#92A8C7` | secondary text |
| Slate Stroke | `#29435F` | circle, separators, guide lines |

## SVG files

- `svg/wunderspec-avatar-github.svg` — square GitHub avatar with dark background.
- `svg/wunderspec-icon-circle-dark.svg` — compact circular icon on dark background.
- `svg/wunderspec-icon-circle-transparent.svg` — transparent compact icon for dark surfaces.
- `svg/wunderspec-mark-transparent.svg` — W constellation mark without enclosing circle.
- `svg/wunderspec-lockup-horizontal-dark.svg` — horizontal logo lockup on dark background.
- `svg/wunderspec-lockup-horizontal-transparent.svg` — horizontal lockup for existing dark backgrounds.
- `svg/wunderspec-readme-header-dark.svg` — wide README/header banner.
- `svg/wunderspec-wordmark-transparent.svg` — wordmark + tagline only.

## PNG exports

- `png/wunderspec-avatar-github-1024.png`
- `png/wunderspec-avatar-github-512.png`
- `png/wunderspec-avatar-github-256.png`
- `png/wunderspec-icon-circle-dark-1024.png`
- `png/wunderspec-icon-circle-dark-512.png`
- `png/wunderspec-icon-circle-transparent-1024.png`
- `png/wunderspec-mark-transparent-1024.png`
- `png/wunderspec-lockup-horizontal-dark-1600.png`
- `png/wunderspec-lockup-horizontal-dark-800.png`
- `png/wunderspec-readme-header-dark-1600x640.png`
- `png/wunderspec-readme-header-dark-1200x480.png`
- `png/wunderspec-wordmark-transparent-900.png`

## Usage suggestions

- GitHub organization/repo avatar: `wunderspec-avatar-github.svg` or `wunderspec-avatar-github-1024.png`.
- README hero image: `wunderspec-readme-header-dark.svg`.
- Website header: `wunderspec-lockup-horizontal-transparent.svg` on a dark navbar.
- Favicon source: `wunderspec-mark-transparent.svg` or `wunderspec-icon-circle-dark.svg`.

## Notes

- Keep the amber accent on only one transition. It reinforces “active state / active transition” without turning the mark into generic astronomy.
- Avoid adding extra stars around the W. The identity works best as a precise state-space diagram with a subtle constellation metaphor.
