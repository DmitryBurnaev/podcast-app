# Icons (SVG) — project structure

## Single source of truth

All inline SVG icons used in templates live as **Jinja2 macros** in `src/templates/snippets/icons.html`. No raw `<svg>...</svg>` in page or snippet templates.

## Usage

- Base layout `src/templates/base.html` imports all icon macros and passes them to every page that extends base.
- Templates call `{{ icon_<name>(...) }}` with optional class override or `id` when needed for JS.
- Snippets that are not rendered in a base-extending context (e.g. `modals.html`) must import the icons they use at the top: `{% from "snippets/icons.html" import icon_close %}`.

## Naming and signature

- Macro name: `icon_<name>`, short, snake_case, semantic (e.g. `icon_search`, `icon_close`, `icon_play`, `icon_filter`, `icon_bolt`, `icon_play_circle`).
- Each macro has at least one parameter `class` with a default (e.g. `class="w-5 h-5"`). Optional params (e.g. `id=none`) only when the icon is toggled or targeted by JS (e.g. play/pause).

## SVG style

- Outline icons: `fill="none" stroke="currentColor"`, plus `stroke-linecap="round"`, `stroke-linejoin="round"`, `stroke-width="2"` where applicable.
- Solid icons (e.g. play triangle): `fill="currentColor"`.
- Use `viewBox="0 0 24 24"`. Class is applied to the root `<svg>` so Tailwind (e.g. `text-slate-400`, `w-4 h-4`) controls size and color.

## Templates that must not contain inline SVG

`episodes.html`, `index.html`, `episodes_detail.html`, `progress.html`, `snippets/modals.html`, and any other page or snippet template. They only call icon macros.
