"""Authenticated, read-only relay for the one managed health ntfy topic."""
from collections.abc import AsyncIterator
import json
from pathlib import Path
from urllib.parse import quote

import anyio
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
import httpx
from sqlalchemy.orm import Session

from .channel_aliases import is_managed
from .health_delivery import Config
from .models import SmartChannel

CONFIG_PATH = Path('/etc/signaldesk/health-delivery.env')
ALLOWED_SINCE = frozenset({'10m','1h','6h','12h','24h','1d'})
MAX_LINE_BYTES = 64 * 1024
MAX_POLL_BYTES = 2 * 1024 * 1024
MAX_POLL_LINES = 2048
HEADERS = {'Cache-Control':'no-store','X-Accel-Buffering':'no'}


def new_upstream_client() -> httpx.AsyncClient:
    # No process proxy, request credential or redirect can change this target.
    return httpx.AsyncClient(timeout=httpx.Timeout(connect=5,read=90,write=5,pool=5),
                             follow_redirects=False,trust_env=False)


class ShieldedCloseStream(httpx.AsyncByteStream):
    """Shield HTTPX's own EOF-close, not just our explicit final close calls."""
    def __init__(self,source):self.source=source

    async def __aiter__(self):
        async for chunk in self.source:
            yield chunk

    async def aclose(self):
        # Response.aclose marks is_closed before awaiting its stream. Without
        # this shield, a cancelled EOF close cannot be retried by later finally.
        with anyio.CancelScope(shield=True):
            await self.source.aclose()


def configuration(db: Session) -> Config:
    try:
        channel = db.get(SmartChannel,'health')
        if channel is None or not is_managed(db,'health'):
            raise ValueError('ManagedHealthRequired')
        server,topic = channel.server,channel.topic
        config = Config.load(CONFIG_PATH)
        if not config.server.startswith('https://') or server != config.server or topic != config.topic:
            raise ValueError('HealthTargetMismatch')
        return config
    except Exception:
        raise HTTPException(503,'健康频道接收暂不可用',headers=HEADERS) from None
    finally:
        # get_db is shared with auth. Authentication has completed; no ORM access
        # follows this close, so an ntfy subscription cannot occupy a DB slot.
        db.close()


def event_line(line: bytes, topic: str) -> bytes:
    try:
        value = json.loads(line.decode('utf-8'))
        if not isinstance(value,dict) or value.get('event') not in {'open','keepalive','message'}:
            raise ValueError('InvalidEvent')
        if value.get('topic') != topic or not isinstance(value.get('id'),str) or not value['id'] or len(value['id'])>256:
            raise ValueError('InvalidIdentity')
        if type(value.get('time')) is not int or value['time']<0:
            raise ValueError('InvalidTime')
        if value['event']=='message' and not isinstance(value.get('message'),str):
            raise ValueError('InvalidMessage')
        for name in ('title','message','click'):
            if name in value and not isinstance(value[name],str):
                raise ValueError('InvalidText')
        if 'priority' in value and (type(value['priority']) is not int or not 1<=value['priority']<=5):
            raise ValueError('InvalidPriority')
        if 'tags' in value and (not isinstance(value['tags'],list) or len(value['tags'])>64
                                or any(not isinstance(tag,str) or len(tag)>200 for tag in value['tags'])):
            raise ValueError('InvalidTags')
        allowed={'id','time','event','title','message','priority','tags','click'}
        clean={key:content for key,content in value.items() if key in allowed}
        clean['topic']='health'
        return json.dumps(clean,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')+b'\n'
    except (ValueError,TypeError,UnicodeError):
        raise ValueError('InvalidHealthFrame') from None


async def event_lines(response: httpx.Response, topic: str, *, poll: bool) -> AsyncIterator[bytes]:
    buffer=bytearray();total=0;lines=0
    # Do not set aiter_bytes(chunk_size): httpx would buffer small live open/
    # keepalive frames until that many bytes arrive, delaying connection setup.
    async for chunk in response.aiter_bytes():
        total+=len(chunk)
        if poll and total>MAX_POLL_BYTES:
            raise ValueError('HealthPollTooLarge')
        buffer.extend(chunk)
        while b'\n' in buffer:
            position=buffer.index(b'\n')
            if position>MAX_LINE_BYTES:
                raise ValueError('HealthLineTooLarge')
            line=bytes(buffer[:position]).strip();del buffer[:position+1]
            if not line:
                continue
            lines+=1
            if poll and lines>MAX_POLL_LINES:
                raise ValueError('TooManyHealthEvents')
            yield event_line(line,topic)
        if len(buffer)>MAX_LINE_BYTES:
            raise ValueError('HealthLineTooLarge')
    if buffer.strip():
        lines+=1
        if poll and lines>MAX_POLL_LINES:
            raise ValueError('TooManyHealthEvents')
        yield event_line(bytes(buffer).strip(),topic)


async def close_upstream(response,client) -> None:
    # Starlette cancels the streaming task on client disconnect. Cleanup must
    # survive that cancellation and release both connection and client pool.
    with anyio.CancelScope(shield=True):
        try:
            if response is not None:
                try:await response.aclose()
                except Exception:pass
        finally:
            if client is not None:
                try:await client.aclose()
                except Exception:pass


class RelayStreamingResponse(StreamingResponse):
    def __init__(self,content,response,client):
        super().__init__(content,media_type='application/x-ndjson',headers=HEADERS)
        self.upstream_response,self.upstream_client=response,client

    async def __call__(self,scope,receive,send):
        try:
            await super().__call__(scope,receive,send)
        finally:
            # Also covers a disconnect while response headers are being sent,
            # before the body generator has ever entered its try/finally.
            await close_upstream(self.upstream_response,self.upstream_client)


async def relay(db: Session, poll: str, since: str):
    if poll not in {'0','1'} or since not in ALLOWED_SINCE:
        db.close()
        raise HTTPException(422,'不支持的健康频道历史范围',headers=HEADERS)
    config=configuration(db)
    client=None;response=None
    try:
        client=new_upstream_client()
        headers={'Accept':'application/x-ndjson','Accept-Encoding':'identity'}
        if config.token:
            headers['Authorization']='Bearer '+config.token
        request=client.build_request('GET',config.server+'/'+quote(config.topic,safe='')+'/json',
                                     params={'poll':poll,'since':since},headers=headers)
        response=await client.send(request,stream=True)
        response.stream=ShieldedCloseStream(response.stream)
        content_type=response.headers.get('content-type','').split(';',1)[0].strip().lower()
        if not 200<=response.status_code<300 or content_type not in {'application/x-ndjson','application/json','application/jsonlines'}:
            raise ValueError('HealthUpstreamUnavailable')
        frames=event_lines(response,config.topic,poll=poll=='1')
        if poll=='1':
            # A finite, bounded poll can be validated before HTTP headers leave.
            data=[frame async for frame in frames]
            await close_upstream(response,client)
            response=client=None
            return StreamingResponse(iter(data),media_type='application/x-ndjson',headers=HEADERS)
        first=await anext(frames)
    except BaseException as error:
        await close_upstream(response,client)
        if not isinstance(error,Exception):
            raise
        raise HTTPException(502,'健康频道上游接收暂不可用',headers=HEADERS) from None

    async def stream():
        try:
            yield first
            async for frame in frames:
                yield frame
        except (httpx.HTTPError,ValueError,UnicodeError):
            # Once headers are sent, close a bad stream without reflecting any
            # upstream exception, URL, body or unknown event to the client.
            return
        finally:
            await close_upstream(response,client)

    return RelayStreamingResponse(stream(),response,client)
