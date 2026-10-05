"""MCP server: render images for display profiles and host them over HTTP."""

from __future__ import annotations

import argparse
import functools
import hmac
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .http_host import build_app
from .profiles import Profile, load_profiles
from .render import RenderError, Renderer, card_elements
from .store import ImageStore

INSTRUCTIONS = """\
Renders images for small displays (e-paper, OLED, TFT) and hosts them over HTTP.
Each display type is a *profile* (resolution + colour depth). An image is identified by
(profile, label); rendering to an existing label overwrites it, so a display can keep fetching
the same URL. This server does not know where images are shown or when to refresh: use the
returned `url` and `changed` flag in whatever pushes the update to the display.
Start with list_profiles. Use render_layout for most screens (rows/columns, auto-fitting text, warnings),
render_card for a quick fixed layout, and render_image only when you need exact pixel placement.
"""


@dataclass
class Config:
    data_dir: Path
    profiles_file: Path | None
    host: str
    port: int
    base_url: str | None
    font: str | None


class Service:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.profiles: dict[str, Profile] = load_profiles(cfg.profiles_file)
        self.store = ImageStore(cfg.data_dir)

    def profile(self, name: str) -> Profile:
        try:
            return self.profiles[name]
        except KeyError:
            raise ValueError(f"unknown profile {name!r}; available: {', '.join(sorted(self.profiles))}") from None

    def render_layout(self, profile_name: str, label: str, root: dict, background: str,
                      description: str) -> dict:
        profile = self.profile(profile_name)
        r = Renderer(profile, self.cfg.font)
        try:
            img, warnings = r.render_layout(root, background)
        except RenderError as exc:
            raise ValueError(str(exc)) from None
        meta = self.store.save(profile.name, label, r.encode(img), profile.format, profile.width,
                               profile.height, description)
        return {**meta, "url": self.url(meta), "warnings": warnings}

    def url(self, meta: dict) -> str:
        base = (self.cfg.base_url or f"http://{self.cfg.host}:{self.cfg.port}").rstrip("/")
        return f"{base}/images/{meta['profile']}/{meta['label']}.{meta['format']}"

    def render(self, profile_name: str, label: str, elements: list[dict], background: str,
               description: str) -> dict:
        profile = self.profile(profile_name)
        r = Renderer(profile, self.cfg.font)
        try:
            data = r.encode(r.render(elements, background))
        except RenderError as exc:
            raise ValueError(str(exc)) from None
        meta = self.store.save(profile.name, label, data, profile.format, profile.width, profile.height,
                               description)
        return {**meta, "url": self.url(meta)}


def reports_errors(fn):
    """Turn input problems (ValueError, RenderError) into ToolError.

    The SDK only shows the caller the message of a ToolError; any other exception reaches the model as
    a bare "Error executing tool", which gives it nothing to correct.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as exc:
            raise ToolError(str(exc)) from None
    return wrapper


def build_server(svc: Service) -> MCPServer:
    mcp = MCPServer("display-image-mcp", instructions=INSTRUCTIONS)

    @mcp.tool()
    @reports_errors
    def list_profiles() -> list[dict]:
        """List display profiles (resolution, colour mode, output format)."""
        return [p.to_dict() for p in svc.profiles.values()]

    @mcp.tool()
    @reports_errors
    def render_card(profile: str, label: str, header: str, lines: list[str], footer: str = "",
                    invert_header: bool = True, description: str = "") -> dict:
        """Render a simple card (header bar, body lines, footer) and store it as (profile, label).

        Text that is too long is truncated with an ellipsis and extra lines beyond what fits are
        dropped, so keep it short. Returns the image URL, its sha256 and `changed` (false when the
        pixels are identical to what was already stored, so a caller can skip a display refresh).
        """
        p = svc.profile(profile)
        return svc.render(profile, label, card_elements(p, header, lines, footer, invert_header),
                          "white", description)

    @mcp.tool()
    @reports_errors
    def render_image(profile: str, label: str, elements: list[dict[str, Any]], background: str = "white",
                     description: str = "") -> dict:
        """Render a custom layout from drawing elements and store it as (profile, label).

        Origin (0,0) is top-left; sizes are in pixels. Colours are names (black, white, red, yellow,
        gray, dark_gray, light_gray) or #rrggbb; they snap to what the profile can show. Elements:
          {"type":"text","text":..,"x":..,"y":..,"size":12,"color":"black","bold":false,
           "anchor":"la","max_width":px,"max_lines":1}   (anchor: Pillow anchors, e.g. "la","mm","ra")
          {"type":"rect","x":..,"y":..,"w":..,"h":..,"fill":colour|null,"outline":colour|null,"width":1}
          {"type":"line","x1":..,"y1":..,"x2":..,"y2":..,"color":"black","width":1}
          {"type":"progress","x":..,"y":..,"w":..,"h":..,"value":0.0-1.0}
        Returns the image URL, sha256 and `changed` (see render_card).
        """
        return svc.render(profile, label, elements, background, description)

    @mcp.tool()
    @reports_errors
    def render_layout(profile: str, label: str, root: dict[str, Any], background: str = "white",
                      description: str = "") -> dict:
        """Lay out a screen from nested rows and columns (no pixel coordinates) and store it as (profile, label).

        `root` fills the whole display. Containers split space between children; text can fit itself.
        Always check `warnings` in the result: it reports truncated text, overflow and elements that got
        no room, so you can fix the layout and render again (same label overwrites).

        Nodes (every node accepts `w`, `h` in px and `flex` as a share of leftover space):
          {"type":"column"|"row","children":[...],"gap":4,"padding":6,"align":"stretch|start|center|end",
           "justify":"start|center|end|space-between","fill":colour,"border":px,"border_color":colour}
              padding is a number, [vertical, horizontal] or [top, right, bottom, left].
              align = cross axis (default stretch); justify = main axis when no child has flex.
          {"type":"text","text":"..","size":12|"fit","bold":false,"color":"black","align":"left|center|right",
           "valign":"top|middle|bottom","max_lines":1,"min_size":8,"max_size":96,"fit":"shrink"}
              size "fit" (or fit "shrink") picks the largest size that fits the box: give it flex or w/h.
              Text longer than its box is cut with an ellipsis (and warned): raise max_lines to wrap.
          {"type":"rect","fill":colour,"outline":colour}       (a bar or rule: {"type":"rect","h":2,"fill":"black"})
          {"type":"progress","value":0.0-1.0,"h":8}
          {"type":"spacer"}                                    (give it flex or w/h)
        Colours: black, white, red, yellow, gray, dark_gray, light_gray or #rrggbb (snapped to the profile).
        Limits: 8 levels deep, 120 nodes.
        """
        return svc.render_layout(profile, label, root, background, description)

    @mcp.tool()
    @reports_errors
    def list_images(profile: str | None = None) -> list[dict]:
        """List stored images (all profiles, or one) with URL, sha256, size and last-changed time."""
        names = [svc.profile(profile).name] if profile else list(svc.profiles)
        return [{**m, "url": svc.url(m)} for n in names for m in svc.store.list(n)]

    @mcp.tool()
    @reports_errors
    def delete_image(profile: str, label: str) -> dict:
        """Delete the image stored for (profile, label)."""
        svc.profile(profile)
        return {"deleted": svc.store.delete(profile, label)}

    return mcp


def parse_args(argv: list[str] | None = None) -> tuple[Config, argparse.Namespace]:
    env = os.environ.get
    ap = argparse.ArgumentParser(prog="display-image-mcp", description=__doc__)
    ap.add_argument("--data-dir", default=env("DISPLAY_MCP_DATA", "./data"), help="where images are stored")
    ap.add_argument("--profiles", default=env("DISPLAY_MCP_PROFILES"), help="TOML file with extra profiles")
    ap.add_argument("--http-host", default=env("DISPLAY_MCP_HTTP_HOST", "0.0.0.0"), help="image host bind address")
    ap.add_argument("--http-port", type=int, default=int(env("DISPLAY_MCP_HTTP_PORT", "8099")), help="image host port")
    ap.add_argument("--base-url", default=env("DISPLAY_MCP_BASE_URL"),
                    help="externally reachable URL of the image host, used in returned links "
                         "(default http://<http-host>:<http-port>)")
    ap.add_argument("--font", default=env("DISPLAY_MCP_FONT"), help="path to a .ttf font (default: Pillow's built-in)")
    ap.add_argument("--token", default=env("DISPLAY_MCP_TOKEN"),
                    help="require 'Authorization: Bearer <token>' on the MCP endpoint (streamable-http only)")
    ap.add_argument("--transport", choices=["stdio", "streamable-http"], default=env("DISPLAY_MCP_TRANSPORT", "stdio"))
    ap.add_argument("--mcp-host", default=env("DISPLAY_MCP_MCP_HOST", "127.0.0.1"), help="MCP bind (streamable-http)")
    ap.add_argument("--mcp-port", type=int, default=int(env("DISPLAY_MCP_MCP_PORT", "8767")))
    a = ap.parse_args(argv)
    cfg = Config(Path(a.data_dir), Path(a.profiles) if a.profiles else None, a.http_host, a.http_port,
                 a.base_url, a.font)
    return cfg, a


class BearerAuth:
    """ASGI middleware: reject HTTP requests without the right bearer token."""

    def __init__(self, app, token: str):
        self.app = app
        self.token = token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            auth = dict(scope["headers"]).get(b"authorization", b"")
            scheme, _, supplied = auth.partition(b" ")
            if scheme.lower() != b"bearer" or not hmac.compare_digest(supplied.strip(), self.token):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain"), (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self.app(scope, receive, send)


def main(argv: list[str] | None = None) -> None:
    cfg, args = parse_args(argv)
    svc = Service(cfg)
    # Image host runs in a thread beside the MCP transport. Logs go to stderr so stdio stays clean.
    app = build_app(svc.store, svc.profiles, cfg.base_url)
    server = uvicorn.Server(uvicorn.Config(app, host=cfg.host, port=cfg.port, log_level="warning"))
    threading.Thread(target=server.run, name="image-host", daemon=True).start()
    print(f"image host on http://{cfg.host}:{cfg.port}", file=sys.stderr)
    mcp = build_server(svc)
    if args.transport == "stdio":
        if args.token:
            print("--token only applies to --transport streamable-http; ignoring", file=sys.stderr)
        mcp.run("stdio")
    else:
        app = mcp.streamable_http_app(host=args.mcp_host)
        if args.token:
            app = BearerAuth(app, args.token)
        elif args.mcp_host not in ("127.0.0.1", "localhost", "::1"):
            print("WARNING: MCP endpoint is open to the network without --token", file=sys.stderr)
        uvicorn.run(app, host=args.mcp_host, port=args.mcp_port, log_level="warning")


if __name__ == "__main__":
    main()
