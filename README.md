# display-image-mcp

An [MCP](https://modelcontextprotocol.io) server that **renders images for small displays** (e-paper, OLED, TFT) and **hosts them over HTTP**.

It deliberately does one thing: turn a description of a screen into a pixel-exact image for a given display, and serve it. It knows nothing about *where* the image is shown or *when* to refresh it, so any agent, automation or device can use it. Whatever drives your display (Home Assistant, Node-RED, an LLM agent) decides when to push, and the display fetches the URL.

## Concepts

- **Profile**: a kind of display: resolution, colour depth and output format. `mono`, `gray4`, `gray16`, `tricolor_red`, `tricolor_yellow` or `rgb`. Output is BMP or PNG. Drawing colours snap to what the profile can show.
- **Label**: a name for an image within a profile. Rendering to an existing label **overwrites** it, so a display can keep polling one stable URL. A profile can hold many labels (e.g. `main`, `alert`, `calendar`).
- **Digest**: `GET /images/<profile>` lists what is available, with sha256, size and last-changed time.

Built-in profiles (see `list_profiles`): `waveshare-2in9-v2` (296×128 mono), `waveshare-2in9-b` (tricolor), `waveshare-2in13-v3`, `waveshare-4in2`, `waveshare-7in5-v2`, `ssd1306-128x64`, `st7789-240x240` (RGB). Add your own with a TOML file:

```toml
# profiles.toml  (pass with --profiles or DISPLAY_MCP_PROFILES)
[profiles.kitchen-lcd]
width = 320
height = 240
color_mode = "rgb"      # mono | gray4 | gray16 | tricolor_red | tricolor_yellow | rgb
format = "png"          # bmp | png
description = "Kitchen 320x240 TFT"
```

Profile and label names are lowercase letters, digits, `-` and `_`.

## MCP tools

| Tool | What it does |
|---|---|
| `list_profiles` | Display profiles available |
| `render_card(profile, label, header, lines, footer?, invert_header?)` | Quick header/lines/footer layout, sized to the display |
| `render_image(profile, label, elements, background?)` | Custom layout from `text`, `rect`, `line` and `progress` elements |
| `list_images(profile?)` | Stored images with URL, sha256 and last-changed time |
| `delete_image(profile, label)` | Remove an image |

Render tools return the image `url`, `sha256` and **`changed`**, which is `false` when the pixels are identical to what was already stored. A caller can use that to skip a needless e-paper refresh.

## HTTP

| Endpoint | |
|---|---|
| `GET /images/<profile>/<label>.<bmp\|png>` | The image (`ETag`, `Cache-Control: no-cache`; extension optional) |
| `GET /images/<profile>` | Digest for one profile |
| `GET /images` | Digest for every profile that has images |
| `GET /profiles` | Profile definitions |

The HTTP host has **no authentication**. Run it on a trusted network and be careful what you render.

## Run

```sh
uv sync
uv run display-image-mcp                                  # MCP over stdio + image host on :8099
uv run display-image-mcp --transport streamable-http      # MCP at http://127.0.0.1:8765/mcp
```

| Option | Env | Default |
|---|---|---|
| `--data-dir` | `DISPLAY_MCP_DATA` | `./data` |
| `--profiles` | `DISPLAY_MCP_PROFILES` | none |
| `--http-host` / `--http-port` | `DISPLAY_MCP_HTTP_HOST` / `_PORT` | `0.0.0.0` / `8099` |
| `--base-url` | `DISPLAY_MCP_BASE_URL` | `http://<http-host>:<http-port>`. **Set this to the address your displays can reach**, as it is used in returned URLs |
| `--font` | `DISPLAY_MCP_FONT` | Pillow's built-in vector font |
| `--transport` | `DISPLAY_MCP_TRANSPORT` | `stdio` |
| `--mcp-host` / `--mcp-port` | `DISPLAY_MCP_MCP_HOST` / `_PORT` | `127.0.0.1` / `8765` |

Example MCP client config (stdio):

```json
{ "mcpServers": { "display-image": {
    "command": "uv",
    "args": ["--directory", "/path/to/display-image-mcp", "run", "display-image-mcp"],
    "env": { "DISPLAY_MCP_BASE_URL": "http://192.168.1.10:8099" } } } }
```

## Example: ESPHome e-paper

The display side just downloads a URL:

```yaml
online_image:
  - platform: online_image
    id: dashboard_image
    url: "http://192.168.1.10:8099/images/waveshare-2in9-v2/main.bmp"
    format: BMP
    type: BINARY
    on_download_finished:
      - component.update: eink_display
```

To change the source without reflashing, expose an API action that calls `online_image.set_url` with a URL argument.

## Development

```sh
uv run pytest
```

## License

MIT
