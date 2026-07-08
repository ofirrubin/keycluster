# Modern knob-driven theme

The three login packs (`dynamic-standard`, `dynamic-half`, `dynamic-clean`) are
now fully driven by CSS variables set by `theme-injector.js` from the
per-realm config returned by `GET /v1/themes/{realm}`. No pack requires
`customCss` to look finished -- native knobs alone reproduce the
"ecommerce-quality" dark card / orange gradient button look that previously
only existed as a ~6.8KB `customCss` blob on the ecommerce realm.

Each pack now ships two stylesheets (`theme.properties` lists both under
`styles=`):

- `resources/css/custom.css` -- shared component layer: fonts, the `:root`
  variable contract, dark-mode overrides, inputs, buttons, links, footer,
  RTL. Identical structure across packs; only the token *values* differ
  (e.g. `clean` uses solid colors and `--card-blur: 0px`, `half` uses a
  transparent `--logo-bg` for its gradient branding pane).
- `resources/css/layout.css` -- the pack-specific page/card structure. This is
  the layer that makes the three packs genuinely different:
  - **standard**: centered glass card floating over a full-bleed radial
    background gradient. Logo + realm name stacked above the card.
  - **half**: CSS Grid split-screen. Left column = branding pane
    (`linear-gradient(var(--secondary-color), var(--primary-color))`, logo +
    realm name, left-aligned), right column = flat form pane, both full
    viewport height. Collapses to a stacked layout (branding banner on top,
    card below) under `max-width: 991px`. RTL flips the columns.
  - **clean**: flat solid card, no `backdrop-filter`, tighter radius,
    minimal shadow -- the "no glass, no gradient background" option.

## CSS variable -> visual map

| Variable | Visual effect |
| --- | --- |
| `--primary-color` | Gradient button end color, link color, focus accent base, standard/clean logo chip background |
| `--secondary-color` | Gradient button start color, half-pack branding pane gradient start, link hover color |
| `--background-color` | Page background base color (radial gradient center on standard/half without an image, solid fill on clean) |
| `--card-bg` | Card surface color/alpha. The injector renders this as an `rgba()` from `cardBg` (hex) + `cardOpacity` when both are set |
| `--card-opacity` | Raw 0-1 opacity knob, set independently of `--card-bg` so pack CSS can reference it directly (e.g. a non-color layer) |
| `--card-blur` | `backdrop-filter: blur()` amount on standard/half glass cards; ignored (0px) on clean by design |
| `--border-radius` | Card corner radius (concentric: logo chip radius derives from it via `calc()`) |
| `--input-border-radius` | Input, password-group, and primary/secondary button corner radius |
| `--input-focus-color` | Input focus border + `--focus-ring` accent color |
| `--icon-color` | Password-toggle eye icon color (both light/dark) |
| `--logo-url` | Header logo `background-image` (standard/clean: centered chip above card; half: left-aligned in the branding pane) |
| `--font-family` | Body/label/title font stack |
| `backgroundUrl` (config, not a var) | Full-bleed `body` background image, applied as an inline style by the injector so it wins over the CSS gradient fallback |

Dark mode: `html.dark-mode` (explicit) or `@media (prefers-color-scheme: dark)`
(system, when no explicit mode is set) swap the token values to the dark
palette defined in each pack's `custom.css`. RTL: `html[dir=rtl]` /
`html.rtl` mirror text alignment everywhere and, on `half`, swap which grid
column holds the branding pane vs. the form.

## Reproducing the ecommerce look with zero customCss

`libs/keycluster-core/src/themes.ts` `THEME_DEFAULTS` was updated to exactly
these values, so **any new realm with no per-realm overrides at all** already
renders this look. The orchestrator's acid test is to apply the same object
(explicitly, so it's realm-scoped and not relying on future default drift) to
the `onguard` realm via `POST /v1/themes/onguard`:

```json
{
  "themeName": "dynamic-standard",
  "primaryColor": "#EA6A2E",
  "secondaryColor": "#F0954A",
  "backgroundColor": "#17140f",
  "cardBg": "#201b17",
  "themeMode": "dark",
  "borderRadius": 20,
  "inputBorderRadius": 12,
  "cardBlur": 18,
  "cardOpacity": 0.74,
  "inputFocusColor": "#EA6A2E",
  "fontFamily": "'Roboto', 'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, 'Heebo', sans-serif",
  "footerText": "Secured by Keycluster",
  "customCss": ""
}
```

No `customCss`. Swap `themeName` to `dynamic-half` or `dynamic-clean` for the
split-screen or flat-card variants -- the same config values apply because
every pack resolves the identical variable contract, just with different
layout.css structure and (on `clean`) a flatter shadow/radius scale.
