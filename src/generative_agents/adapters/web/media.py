"""Conditional, streaming delivery of package-owned images."""
from collections import OrderedDict
from io import BytesIO
import mimetypes
import os
from contextlib import nullcontext
from email.utils import formatdate
from pathlib import Path
from threading import RLock

from fastapi import HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from generative_agents.ga_protocol.packages.io import checked_package_path, read_json, open_shared_reader
from generative_agents.ga_protocol.packages.reading import open_readonly_package, validated_experiment
from generative_agents.ga_protocol.schemas.manifests import validate_package_path
from generative_agents.ga_protocol.packages.locking import package_lock

_thumbnails = OrderedDict()
_thumbnail_lock = RLock()


def _thumbnail(path, digest, width):
    key = (digest, width)
    with _thumbnail_lock:
        if key in _thumbnails:
            _thumbnails.move_to_end(key)
            return _thumbnails[key]
    from PIL import Image, ImageOps
    try:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source)
            image.thumbnail((width, width), Image.Resampling.LANCZOS)
            if image.mode not in {'RGB', 'RGBA'}:
                image = image.convert('RGBA' if 'transparency' in image.info else 'RGB')
            output = BytesIO()
            image.save(output, format='WEBP', quality=82)
            content = output.getvalue()
    except (OSError, ValueError) as exc:
        raise HTTPException(422, 'This asset does not support image previews') from exc
    with _thumbnail_lock:
        _thumbnails[key] = content
        while len(_thumbnails) > 128 or sum(map(len, _thumbnails.values())) > 16 * 1024 ** 2:
            _thumbnails.popitem(last=False)
    return content


class _SnapshotResponse(StreamingResponse):
    """Close snapshot handles even if sending fails or the client disconnects."""
    def __init__(self, iterator, close, **kwargs):
        self._close = close
        super().__init__(iterator, **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._close()


def package_asset_response(package, relative, request: Request, *, integrity_prefix=''):
    try:
        relative = validate_package_path(relative)
        if integrity_prefix:
            integrity_prefix = validate_package_path(integrity_prefix)
    except ValueError as exc:
        raise HTTPException(404, 'Package asset path is invalid') from exc
    width = request.query_params.get('width')
    if width is not None:
        if width not in {'96', '192', '384'}:
            raise HTTPException(422, 'Image preview width must be 96, 192 or 384')
        width = int(width)
    context = open_readonly_package(Path(package))
    root = context.__enter__()
    retained, reader = False, None
    closed = False

    def close():
        nonlocal closed
        if not closed:
            closed = True
            try:
                if reader is not None:
                    reader.__exit__(None, None, None)
            finally:
                context.__exit__(None, None, None)

    try:
        content_root = root / integrity_prefix if integrity_prefix else root
        immutable = bool(integrity_prefix) or Path(package).is_file()
        # Snapshot the file handle under the draft writer's lock. Network transfer
        # runs after releasing the lock and cannot block a subsequent draft save.
        with nullcontext() if immutable else package_lock(content_root):
            validated_experiment(content_root)
            target = checked_package_path(root / relative)
            asset_root = checked_package_path(content_root / 'assets')
            try:
                logical = target.relative_to(content_root).as_posix()
                target.relative_to(asset_root)
            except ValueError as exc:
                raise HTTPException(404, 'Package asset path is invalid') from exc
            digest = read_json(content_root / 'integrity/sha256.json')['files'].get(logical)
            if not digest or not target.is_file():
                raise HTTPException(404, 'Package asset is not present')
            suffix = f'-preview-{width}' if width else ''
            etag = f'"{digest}{suffix}"'
            headers = {'ETag': etag, 'X-Content-Type-Options': 'nosniff',
                       'Cache-Control': 'public, max-age=31536000, immutable' if immutable else 'no-cache'}
            matches = [item.strip().removeprefix('W/') for item in request.headers.get('if-none-match', '').split(',')]
            if '*' in matches or etag in matches:
                return Response(status_code=304, headers=headers)
            if width:
                return Response(_thumbnail(target, digest, width), media_type='image/webp', headers=headers)
            reader = open_shared_reader(target)
            handle = reader.__enter__()
            metadata = os.fstat(handle.fileno())
        size = metadata.st_size
        headers.update({'Accept-Ranges': 'bytes', 'Last-Modified': formatdate(metadata.st_mtime, usegmt=True)})
        start, end, status = 0, size-1, 200
        requested = request.headers.get('range', '')
        if requested.startswith('bytes=') and ',' not in requested and request.headers.get('if-range', etag) in {etag, headers['Last-Modified']}:
            try:
                lower, upper = requested[6:].split('-', 1)
                if lower:
                    start = int(lower)
                    end = min(int(upper), size-1) if upper else size-1
                else:
                    length = int(upper)
                    if length <= 0:
                        raise ValueError('invalid suffix')
                    start = max(0, size-length)
                if start < 0 or start >= size or end < start:
                    raise ValueError('unsatisfiable range')
            except ValueError:
                return Response(status_code=416, headers={**headers, 'Content-Range': f'bytes */{size}'})
            headers['Content-Range'] = f'bytes {start}-{end}/{size}'
            status = 206
        headers['Content-Length'] = str(max(0, end-start+1))

        def stream():
            handle.seek(start)
            remaining = end-start+1
            while remaining > 0:
                chunk = handle.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

        retained = True
        return _SnapshotResponse(stream(), close, status_code=status,
                                 media_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream',
                                 headers=headers)
    finally:
        if not retained:
            close()
