# Contact Sheet Templates

The **Contact Sheet** section in the Export sidebar can load layout presets from plain
`.toml` files. **Default** is your in-app baseline layout (factory 600 / 16 / 32 / 38 until
you change it).

---

## Folder

Place template files here:

```
~/NegPy/contact_sheets/
```

On Windows this is typically:

```
C:\Users\<you>\NegPy\contact_sheets\
```

NegPy creates the folder on startup. You can also click **Save as template** in the app
to write a file from the current layout settings.

---

## File format

Each template is a UTF-8 TOML file with a display name and a `[layout]` table:

```toml
name = "Tight 35mm"

[layout]
cell_px = 400
gap = 8
margin = 16
max_tiles = 48
```

| Key | Meaning | Allowed range |
|---|---|---|
| `cell_px` | Long edge of each tile cell (pixels) | 100–4000 |
| `gap` | Space between cells (pixels) | 0–200 |
| `margin` | Black border around the grid (pixels) | 0–500 |
| `max_tiles` | Frames per sheet before pagination | 1–200 |

Omitted keys fall back to the built-in defaults (600 / 16 / 32 / 38).

The optional top-level `name` field is what appears in the **Template** dropdown. If
omitted, the filename stem is used (without `.toml`).

---

## Examples

**NegPy factory default (reference — not required as a file)**

```toml
name = "NegPy default"

[layout]
cell_px = 600
gap = 16
margin = 32
max_tiles = 38
```

**Large cells, fewer per page**

```toml
name = "Large cells"

[layout]
cell_px = 900
gap = 20
margin = 40
max_tiles = 12
```

---

## Behaviour in the app

- **Default** selected → loads your saved Default layout (starts at factory 600 / 16 / 32 / 38).
- **Named template** selected → loads that `.toml` file into the spinboxes.
- **Editing spinboxes** while a template is selected updates that template automatically
  (Default → saved in app settings; named → rewritten `.toml` file). Changes debounce ~500 ms.
- **Save as template** creates a **new** named file from the current spinbox values.
- Invalid or unreadable files are ignored when building the list.
- If a saved template file is deleted, the app falls back to **Default** on next launch.

Output folder, tile rendering, and JPEG naming are unchanged — templates only control grid layout.
