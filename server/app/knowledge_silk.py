"""Bounded offline SILK decoder. Invoked only in an isolated subprocess."""
import ctypes
import os
from pathlib import Path
import stat
import sys
import tempfile
import wave

MAX_INPUT = 20 * 1024 * 1024
MAX_SECONDS = 600
PCM_RATE = 24000
SILK_EXTENSIONS = {".silk", ".slk", ".aud"}


def is_silk(path):
    try:
        with Path(path).open("rb") as stream: header = stream.read(10)
        return header.startswith((b"#!SILK_V3", b"\x02#!SILK_V3"))
    except OSError:
        return False


def validate_packets(data):
    """Reject incomplete lengths before entering the native SDK's fixed buffers."""
    if data.startswith(b"\x02#!SILK_V3"): position = 10
    elif data.startswith(b"#!SILK_V3"): position = 9
    else: raise ValueError("Invalid SILK header")
    packets = 0
    while position < len(data):
        if position + 2 > len(data): raise ValueError("Incomplete SILK length")
        size = int.from_bytes(data[position:position + 2], "little", signed=True); position += 2
        if size == -1:
            if position != len(data): raise ValueError("Trailing SILK data")
            break
        if not 0 < size <= 5120 or position + size > len(data): raise ValueError("Invalid SILK packet")
        position += size; packets += 1
        if packets > MAX_SECONDS * 50: raise ValueError("Too many SILK packets")
    # The SDK's initial jitter buffer requires multiple complete packets.
    if packets < 3: raise ValueError("SILK voice is too short")
    return packets


def decode(source, target):
    source, target = Path(source), Path(target)
    if not source.is_absolute() or source.is_symlink() or source.resolve() != source: raise ValueError("Invalid source")
    if not target.is_absolute() or target.exists() or target.is_symlink() or target.suffix != ".wav" or not target.parent.is_dir(): raise ValueError("Invalid target")
    if target.parent.resolve() != target.parent: raise ValueError("Invalid target directory")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(source, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= MAX_INPUT: raise ValueError("Invalid source size")
        data = stream.read(MAX_INPUT + 1); after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(data) != before.st_size: raise ValueError("Source changed")
    validate_packets(data)
    import pilk
    with tempfile.TemporaryDirectory(prefix=".silk-", dir=target.parent) as work:
        work = Path(work); snapshot = work / "source.silk"; snapshot.write_bytes(data)
        pcm = work / "decoded.pcm"
        # pilk's convenience wrapper uses mktemp and hides SDK diagnostics. Use
        # a private directory and reject SDK error output instead of accepting PLC.
        with tempfile.TemporaryFile() as diagnostics:
            saved = os.dup(1)
            try:
                os.dup2(diagnostics.fileno(), 1)
                pilk.decode(str(snapshot), str(pcm), pcm_rate=PCM_RATE)
                ctypes.CDLL(None if os.name == "posix" else "ucrtbase").fflush(None)
            finally:
                os.dup2(saved, 1); os.close(saved)
            diagnostics.seek(0)
            if diagnostics.read(1): raise ValueError("SILK SDK rejected packet")
        size = pcm.stat().st_size
        if not 0 < size <= PCM_RATE * 2 * MAX_SECONDS or size % 2: raise ValueError("Invalid decoded PCM")
        created = False
        try:
            with target.open("xb") as out:
                created = True
                with wave.open(out, "wb") as audio:
                    audio.setparams((1, 2, PCM_RATE, 0, "NONE", "NONE"))
                    with pcm.open("rb") as raw:
                        while block := raw.read(65536): audio.writeframesraw(block)
            with wave.open(str(target), "rb") as audio:
                if audio.getnframes() * 2 != size: raise ValueError("Incomplete decoded WAV")
        except Exception:
            if created: target.unlink(missing_ok=True)
            raise

def main():
    if len(sys.argv) != 3: raise SystemExit(2)
    if os.name == "posix":
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE, (40 * 1024 * 1024, 40 * 1024 * 1024))
    try:
        decode(*sys.argv[1:])
    except Exception: raise SystemExit(3) from None

if __name__ == "__main__": main()
