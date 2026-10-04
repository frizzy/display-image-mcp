"""Read-only HTTP host for rendered images plus a per-profile digest."""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .profiles import Profile
from .store import ImageStore, check_label

NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}


def build_app(store: ImageStore, profiles: dict[str, Profile], base_url: str | None) -> Starlette:
    def root(request: Request) -> str:
        return (base_url or str(request.base_url)).rstrip("/")

    def image_url(request: Request, meta: dict) -> str:
        return f"{root(request)}/images/{meta['profile']}/{meta['label']}.{meta['format']}"

    def digest_entry(request: Request, meta: dict) -> dict:
        return {**meta, "url": image_url(request, meta)}

    def profile_digest(request: Request, profile: str) -> dict:
        return {
            "profile": profiles[profile].to_dict(),
            "images": [digest_entry(request, m) for m in store.list(profile)],
        }

    async def index(request: Request) -> Response:
        return JSONResponse({
            "profiles": f"{root(request)}/profiles",
            "images": f"{root(request)}/images",
            "image": f"{root(request)}/images/{{profile}}/{{label}}.{{format}}",
        })

    async def healthz(request: Request) -> Response:
        return Response("ok", media_type="text/plain")

    async def list_profiles(request: Request) -> Response:
        return JSONResponse([p.to_dict() for p in profiles.values()])

    async def all_digests(request: Request) -> Response:
        out = {name: profile_digest(request, name) for name in profiles}
        return JSONResponse({k: v for k, v in out.items() if v["images"]}, headers=NO_CACHE)

    async def one_digest(request: Request) -> Response:
        name = request.path_params["profile"]
        if name not in profiles:
            return JSONResponse({"error": f"unknown profile {name!r}"}, status_code=404)
        return JSONResponse(profile_digest(request, name), headers=NO_CACHE)

    async def image(request: Request) -> Response:
        name = request.path_params["profile"]
        label = request.path_params["label"].rsplit(".", 1)[0]  # allow an optional extension
        try:
            check_label(label)
            found = store.get(name, label) if name in profiles else None
        except ValueError:
            found = None
        if found is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        meta, data = found
        etag = f'"{meta["sha256"][:16]}"'
        headers = {**NO_CACHE, "ETag": etag, "Content-Length": str(len(data))}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return Response(data, media_type=meta["content_type"], headers=headers)

    return Starlette(routes=[
        Route("/", index),
        Route("/healthz", healthz),
        Route("/profiles", list_profiles),
        Route("/images", all_digests),
        Route("/images/{profile}", one_digest),
        Route("/images/{profile}/{label}", image),
    ])
