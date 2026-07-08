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
  - **clean**: flat solid card, tighter radius, minimal shadow -- the "no
    glass, no gradient background" option. Blur/opacity knobs are still wired
    (see below); they're just inert at the pack's own opaque defaults.

Every variable in the contract is wired on **every** pack -- including
`--card-blur` and `--card-opacity` on `half`/`clean`, which default to an
opaque card (nothing to blur, by design) but genuinely respond the moment a
realm lowers `cardOpacity` below 1. `.card-pf` on all three packs carries
`backdrop-filter: blur(var(--card-blur, 0px))`, and its border color runs
through `color-mix(in srgb, var(--card-border) calc(var(--card-opacity, 1) *
100%), transparent)` so the raw opacity knob has an effect independent of
whatever alpha the injector already baked into `--card-bg`.

## CSS variable -> visual map

| Variable | Visual effect | standard | half | clean |
| --- | --- | --- | --- | --- |
| `--primary-color` | Gradient button end color, link color, focus accent base, logo chip / branding-pane gradient | yes | yes | yes |
| `--secondary-color` | Gradient button start color, half-pack branding pane gradient start, link hover color | yes | yes | yes |
| `--background-color` | Page background base color (radial gradient center on standard, solid fill on half/clean) | yes | yes | yes |
| `--card-bg` | Card surface color/alpha (injector renders `rgba()` from `cardBg` + `cardOpacity` when both are set) | yes | yes | yes |
| `--card-opacity` | Raw 0-1 knob; fades the card border via `color-mix()` independent of `--card-bg`'s own alpha | yes (border) | yes (separator border) | yes (border) |
| `--card-blur` | `backdrop-filter: blur()` on `.card-pf`; visibly frosts the glass card on standard by default, and on half/clean the moment `cardOpacity < 1` makes the card translucent | yes (default-visible) | yes (visible once translucent) | yes (visible once translucent) |
| `--border-radius` | Card corner radius (concentric: logo chip radius derives from it via `calc()`) | yes | yes | yes |
| `--input-border-radius` | Input, password-group, and primary/secondary button corner radius | yes | yes | yes |
| `--input-focus-color` | Input focus border + `--focus-ring` accent color | yes | yes | yes |
| `--icon-color` | Password-toggle eye icon color (both light/dark) | yes | yes | yes |
| `--logo-url` | Header logo `background-image` (standard/clean: centered chip above card; half: left-aligned in the branding pane) | yes | yes | yes |
| `--font-family` | Body/label/title font stack | yes | yes | yes |
| `backgroundUrl` (config, not a var) | Full-bleed `body` background image, applied as an inline style by the injector so it wins over the CSS gradient fallback | yes | yes | yes |

Dark mode: `html.dark-mode` (explicit) or `@media (prefers-color-scheme: dark)`
(system, when no explicit mode is set) swap the token values to the dark
palette defined in each pack's `custom.css`. RTL: `html[dir=rtl]` /
`html.rtl` mirror text alignment everywhere and, on `half`, swap which grid
column holds the branding pane vs. the form.

Every glow/box-shadow/focus-ring that reads as a brand color is derived from
`--primary-color` via `color-mix(in srgb, var(--primary-color) X%,
transparent)` -- never a hardcoded hex/rgba. The only literal color values
left in the CSS are neutral black/white elevation tints (`--card-shadow`,
input shadows) and the `:root` *default* values of the color vars themselves,
which have to be concrete somewhere.

Buttons render as `<button>`/`<input type=submit>` on the login form but as
`<a class="pf-c-button pf-m-primary pf-m-block">` on error/info/logout pages.
`.pf-c-button.pf-m-primary` (and `.pf-m-secondary`) carry `display:flex;
align-items:center; justify-content:center; text-decoration:none` so the
label centers on both axes and gets the gradient/border regardless of tag.

Inputs are explicit `width:100% !important` (not left to Keycloak's own
PatternFly base CSS) so two-column layouts (register, update-profile) fill
their column instead of shrinking to content.

The remember-me / consent checkbox (`.checkbox input[type=checkbox]`,
`.login-pf-settings input[type=checkbox]`, and the PatternFly
`.pf-c-check__input`) is restyled into a custom control: native box hidden via
`appearance:none`, a rounded box drawn with `--input-border-radius`, filled
with `--primary-color` + a CSS-drawn checkmark (`::after` border-clip, no
image asset) on `:checked`, and a `--focus-ring` outline on
`:focus-visible`. Same markup coverage in all three packs.

## Animation knob

`animation` (`"none" | "subtle" | "playful"`, default `"subtle"`) is a native
knob in `ThemeConfigSchema` (`libs/keycluster-core/src/theme-schema.ts`) and
`THEME_DEFAULTS`. `theme-injector.js` sets `<html class="anim-none |
anim-subtle | anim-playful">` from the resolved config -- the same class
contract the magma theme-studio preview (`ThemePreview.tsx`) sets, so the
live page and the offline preview animate identically.

| Level | What it does |
| --- | --- |
| `none` | `html.anim-none *,*::before,*::after { transition:none; animation:none }` -- every hover/press/enter effect off. |
| `subtle` (default) | The hover/press transitions already defined in the component layer (border-color/box-shadow fades, `scale(0.98)` button press) plus a gentle card enter: `kc-card-enter` keyframe, fade + 12px rise, `0.42s cubic-bezier(0.2, 0, 0, 1)`. |
| `playful` | Same enter keyframe but `0.5s cubic-bezier(0.16, 1, 0.3, 1)`; primary-button hover/press transitions speed up to `0.12s` and lift `translateY(-2px)` on hover. No overshoot/spring bounce anywhere -- snappier and more pronounced, never jarring. |

`@media (prefers-reduced-motion: reduce)` forces `transition:none;
animation:none` on every element as a CSS-level backstop, independent of the
JS class -- the injector also checks `matchMedia('(prefers-reduced-motion:
reduce)')` itself and overrides the configured level to `none`, live, via a
`change` listener (covers the OS setting flipping mid-session).

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
  "animation": "subtle",
  "customCss": ""
}
```

No `customCss`. Swap `themeName` to `dynamic-half` or `dynamic-clean` for the
split-screen or flat-card variants -- the same config values apply because
every pack resolves the identical variable contract, just with different
layout.css structure and (on `clean`) a flatter shadow/radius scale.
