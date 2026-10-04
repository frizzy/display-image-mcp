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

The image host has **no authentication**, because displays like an ESP32 can't easily send headers. Run it on a trusted network and be careful what you render. The MCP endpoint can be protected with a bearer token (see below).

## Run

```sh
uv sync
uv run display-image-mcp                                  # MCP over stdio + image host on :8099
uv run display-image-mcp --transport streamable-http      # MCP at http://127.0.0.1:8767/mcp
```

| Option | Env | Default |
|---|---|---|
| `--data-dir` | `DISPLAY_MCP_DATA` | `./data` |
| `--profiles` | `DISPLAY_MCP_PROFILES` | none |
| `--http-host` / `--http-port` | `DISPLAY_MCP_HTTP_HOST` / `_PORT` | `0.0.0.0` / `8099` |
| `--base-url` | `DISPLAY_MCP_BASE_URL` | `http://<http-host>:<http-port>`. **Set this to the address your displays can reach**, as it is used in returned URLs |
| `--font` | `DISPLAY_MCP_FONT` | Pillow's built-in vector font |
| `--transport` | `DISPLAY_MCP_TRANSPORT` | `stdio` |
| `--token` | `DISPLAY_MCP_TOKEN` | none. If set, the MCP endpoint requires `Authorization: Bearer <token>` (streamable-http only) |
| `--mcp-host` / `--mcp-port` | `DISPLAY_MCP_MCP_HOST` / `_PORT` | `127.0.0.1` / `8767` |

## Run as a standalone service

The server is meant to run on its own, independent of whichever agent uses it: the agent just connects to a URL and never starts or stops the process. Use the `streamable-http` transport and let your OS service manager own the lifecycle.

Install it as a `uv` tool (no checkout needed):

```sh
uv tool install git+https://github.com/frizzy/display-image-mcp   # puts display-image-mcp in ~/.local/bin
uv tool upgrade display-image-mcp                                 # later, to update
```

Check it by hand first (replace the IP with this machine's LAN address):

```sh
display-image-mcp --transport streamable-http --mcp-host 0.0.0.0 \
  --data-dir ~/display-images --base-url http://192.168.1.10:8099
```

- MCP endpoint: `http://192.168.1.10:8767/mcp`
- Image host: `http://192.168.1.10:8099`

`--mcp-host 0.0.0.0` lets an agent on another machine connect. Protect the MCP endpoint with a token: generate one with `openssl rand -hex 32` and pass it as `--token` or `DISPLAY_MCP_TOKEN`; clients then send `Authorization: Bearer <token>`. Without a token the server prints a warning when bound to a non-local address. Keep both ports on a trusted network either way, or leave `--mcp-host` at its `127.0.0.1` default if the agent runs on the same machine.

### Linux (systemd)

`/etc/systemd/system/display-image-mcp.service`. Adjust `User` and the paths. `uv tool` installs into that user's `~/.local/bin`:

```ini
[Unit]
Description=display-image-mcp
After=network-online.target
Wants=network-online.target

[Service]
User=pi
ExecStart=/home/pi/.local/bin/display-image-mcp --transport streamable-http --mcp-host 0.0.0.0
Environment=DISPLAY_MCP_DATA=/home/pi/display-images
Environment=DISPLAY_MCP_BASE_URL=http://192.168.1.10:8099
Environment=DISPLAY_MCP_TOKEN=put-a-long-random-token-here
# Environment=DISPLAY_MCP_PROFILES=/home/pi/display-profiles.toml
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now display-image-mcp
journalctl -u display-image-mcp -f
```

### macOS (launchd)

`~/Library/LaunchAgents/com.example.display-image-mcp.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.example.display-image-mcp</string>
  <key>ProgramArguments</key><array>
    <string>/Users/you/.local/bin/display-image-mcp</string>
    <string>--transport</string><string>streamable-http</string>
    <string>--mcp-host</string><string>0.0.0.0</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>DISPLAY_MCP_DATA</key><string>/Users/you/display-images</string>
    <key>DISPLAY_MCP_BASE_URL</key><string>http://192.168.1.10:8099</string>
    <key>DISPLAY_MCP_TOKEN</key><string>put-a-long-random-token-here</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>/tmp/display-image-mcp.log</string>
</dict></plist>
```

```sh
launchctl load ~/Library/LaunchAgents/com.example.display-image-mcp.plist
```

The unit files above follow standard systemd and launchd usage. I have not run them myself: only the `uv tool install` command and the LAN-bound streamable-http endpoint were tested.

## Running with Docker

A multi-arch image (amd64 and arm64, so a Raspberry Pi works too) is published to GitHub Container Registry as `ghcr.io/frizzy/display-image-mcp`, built by CI from `main` (`:latest`) and from `v*` tags (`:X.Y.Z`, `:X.Y`). To build it yourself, run `docker build -t display-image-mcp .` in a checkout and use that name instead.

### Always on, with Docker Compose

[`docker-compose.yml`](docker-compose.yml) runs the streamable-HTTP server with a restart policy, a health check, a read-only filesystem, no capabilities and a volume for the images:

```sh
DISPLAY_MCP_BASE_URL=http://192.168.1.10:8099 docker compose up -d   # or put that line in a .env file
docker compose logs -f
docker compose pull && docker compose up -d                          # update
```

| Port | What | Published to |
|---|---|---|
| `8099` | Image host. Displays fetch from here | your network |
| `8767` | MCP endpoint, `http://127.0.0.1:8767/mcp` | this machine only |

The MCP endpoint is bound to localhost by default. To let an agent on another machine connect, set `DISPLAY_MCP_TOKEN` (in `.env` or the environment) and change the mapping to `"8767:8767"`. To add your own profiles, mount a TOML file and set `DISPLAY_MCP_PROFILES` (there is a commented example in the compose file).

### Plain `docker run`

```sh
docker run -d --name display-image-mcp --restart unless-stopped \
  -p 8099:8099 -p 127.0.0.1:8767:8767 -v display-images:/data \
  -e DISPLAY_MCP_BASE_URL=http://192.168.1.10:8099 -e DISPLAY_MCP_TOKEN=<token> \
  ghcr.io/frizzy/display-image-mcp --transport streamable-http --mcp-host 0.0.0.0
```

Without `--transport` the container speaks MCP over stdio (`docker run -i --rm ...`), but then whatever launches it owns its lifecycle.

### Connecting an agent

Point any MCP client that supports streamable HTTP at the URL, e.g. `http://192.168.1.10:8767/mcp`, with the header `Authorization: Bearer <token>` if you set a token. Check your client's docs for how it takes a remote server URL and headers. If a client can only launch local stdio servers, you can still use `display-image-mcp` (no flags) as its command, but then that client owns the process lifecycle, which is what this setup avoids.

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
