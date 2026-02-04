---
name: adding-svg-icons
description: Add new SVG icons as Jinja2 macros in templates/snippets/icons.html, register in base.html, use only via macro in templates. Use when adding a new icon, embedding SVG in the podcast app templates, or when the user asks to add an icon.
---

# Adding SVG Icons

## Rule

**New SVG icon → add macro in `snippets/icons.html` → add to base imports → use only via macro in templates. No inline SVG in page/snippet templates.**

## Workflow

1. **Add macro** in `src/templates/snippets/icons.html`:
   - `{% macro icon_<name>(class="w-5 h-5", ...) %}`
   - Put `<svg>` inside; use `class="{{ class }}"` on the root `<svg>`.
   - Add optional param (e.g. `id=none`) only if the icon is toggled or targeted by JS.
2. **Register in base:** add the macro name to the `{% from "snippets/icons.html" import ... %}` list in `src/templates/base.html`.
3. **Use in templates:** call `{{ icon_<name>("optional-class-override") }}` or `{{ icon_<name>(id="some-id") }}`. Do **not** paste raw `<svg>...</svg>` into templates.
4. **Snippets that don't extend base:** if a snippet (e.g. `modals.html`) uses an icon, add `{% from "snippets/icons.html" import icon_<name> %}` at the top of that file.

## Macro conventions

- **Naming:** `icon_<name>`, snake_case, semantic (e.g. `icon_filter`, `icon_play`, `icon_play_circle`).
- **SVG:** outline icons use `fill="none" stroke="currentColor"`; solid use `fill="currentColor"`. Use `viewBox="0 0 24 24"`, `stroke-linecap="round"`, `stroke-linejoin="round"`, `stroke-width="2"` where applicable. Class on `<svg>` so Tailwind controls size/color.

## Additional reference

For full structure (single source of truth, usage, no inline SVG), see [reference.md](reference.md).
