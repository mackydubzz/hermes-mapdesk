"""Authenticated Hermes-scoped HTTP endpoints for the native MapDesk pane."""
from pathlib import Path

from fastapi import APIRouter, Response, HTTPException, WebSocket
from starlette.websockets import WebSocketDisconnect
import asyncio
import importlib.util
import json

router = APIRouter()
ROOT = Path(__file__).resolve().parent.parent
COMMAND_CAP_BYTES = 16 * 1024 * 1024

# Live sync (X1): every /events websocket plus the host event loop captured on
# the first connect. The plugin host runs one loop for all connections, so the
# /command handler (a threadpool thread) can schedule fire-and-forget pushes.
_EVENT_SOCKETS = set()
_EVENT_LOOP = None


def _saved_token():
    """The saved setting with surrounding whitespace removed (a pasted token often carries a newline)."""
    from agent.secret_scope import get_secret
    value = get_secret('MAPDESK_MAPBOX_ACCESS_TOKEN') or ''
    return value.strip() if isinstance(value, str) else ''


def public_token():
    """Only the scoped, display-only token can cross into the map sandbox."""
    value = _saved_token()
    return value if value.startswith('pk.') else ''


def token_rejected():
    """True when something is saved but it is not a public pk. token, so it is ignored.

    Only this fact leaves the backend, never the value: the pane uses it to
    say why the free basemap is showing although a token was entered."""
    try:
        value = _saved_token()
    except Exception:
        return False
    return bool(value) and not value.startswith('pk.')


@router.get('/status')
def status():
    return {'plugin': 'mapdesk', 'version': '0.3.0',
            'token_configured': bool(public_token())}


@router.get('/bootstrap')
def bootstrap(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    token = public_token()
    return {'token': token, 'basemap': 'mapbox' if token else 'keyless',
            'token_rejected': False if token else token_rejected()}


@router.get('/renderer')
def renderer():
    # The guest choice follows token presence ONLY, never a load failure.
    kind = 'mapbox' if public_token() else 'keyless'
    name = 'map.html' if kind == 'mapbox' else 'map_keyless.html'
    path = ROOT / 'desktop' / name
    try:
        html = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        # The chosen guest file is not installed yet; serve the other file
        # rather than a 500. Both guests speak the same protocol.
        fallback = 'map_keyless.html' if kind == 'mapbox' else 'map.html'
        html = (ROOT / 'desktop' / fallback).read_text(encoding='utf-8')
    return {'html': html, 'kind': kind}


_RUNTIME = None


def runtime():
    global _RUNTIME
    if _RUNTIME is None:
        spec = importlib.util.spec_from_file_location('mapdesk_http_runtime', ROOT / 'mapdesk_runtime.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _RUNTIME = module
    return _RUNTIME


def _current_revision():
    """Cheap workspace revision read for live-sync pushes (X1).

    Opens the store via the runtime module's `_store()` (mapdesk_core is
    already loaded by any prior command, so no heavy imports happen here)
    and reads only the workspace metadata row. Returns None when the
    workspace is unavailable so callers can skip the push instead of
    broadcasting a bogus revision.
    """
    try:
        store = runtime()._store()
        with store._connection() as connection:
            revision = store._metadata(connection).get('revision')
        return revision if type(revision) is int else 0
    except Exception:
        return None


async def _fan_out_revision(revision):
    """Push the new revision to every connected socket; drop dead ones."""
    for socket in list(_EVENT_SOCKETS):
        try:
            await socket.send_json({'type': 'revision', 'revision': revision})
        except Exception:
            _EVENT_SOCKETS.discard(socket)


def _broadcast_revision():
    """Fire-and-forget live-sync push after a mutating command; never blocks."""
    if not _EVENT_SOCKETS or _EVENT_LOOP is None:
        return

    async def _push():
        revision = await asyncio.to_thread(_current_revision)
        if revision is not None:
            await _fan_out_revision(revision)

    try:
        asyncio.run_coroutine_threadsafe(_push(), _EVENT_LOOP)
    except RuntimeError:
        pass  # the loop closed between the check and the schedule; retry next command


@router.websocket('/events')
async def events(websocket: WebSocket):
    """Live sync (X1): hello on connect, revision pushes after mutations."""
    global _EVENT_LOOP
    _EVENT_LOOP = asyncio.get_running_loop()
    await websocket.accept()
    _EVENT_SOCKETS.add(websocket)
    try:
        revision = await asyncio.to_thread(_current_revision)
        await websocket.send_json({'type': 'hello', 'revision': 0 if revision is None else revision})
        while True:
            message = await websocket.receive()
            if message.get('type') == 'websocket.disconnect':
                break
    except WebSocketDisconnect:
        pass
    finally:
        _EVENT_SOCKETS.discard(websocket)


@router.get('/state')
def state():
    return runtime().execute('state', {})


@router.get('/summary')
def summary():
    return runtime().execute('summary', {})


@router.get('/layer/{layer_id}')
def layer(layer_id: str):
    try:
        text = runtime().layer_text(layer_id)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    # The stored JSON text goes out as is: no parse and re-encode of the coordinates.
    return Response(content=text, media_type='application/json')


@router.get('/selection')
def selection():
    return runtime().execute('selection', {})


@router.post('/geocode')
def geocode(payload: dict):
    if not isinstance(payload, dict) or not isinstance(payload.get('addresses'), list):
        raise HTTPException(400, 'Expected {"addresses": [...]}')
    try:
        return runtime().execute('geocode_batch', {'addresses': payload['addresses']})
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    except OSError:
        raise HTTPException(503, 'Geocoder unavailable; no changes confirmed') from None


@router.post('/command')
def command(payload: dict):
    if set(payload) != {'action', 'args'} or not isinstance(payload['action'], str) or not isinstance(payload['args'], dict):
        raise HTTPException(400, 'Expected action and args')
    if len(json.dumps(payload).encode('utf-8')) > COMMAND_CAP_BYTES:
        raise HTTPException(413, 'Command exceeds 16 MiB')
    try:
        result = runtime().execute(payload['action'], payload['args'])
    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(400, str(error)) from None
    except OSError:
        raise HTTPException(503, 'Source or workspace unavailable; no changes confirmed') from None
    _broadcast_revision()
    return result
