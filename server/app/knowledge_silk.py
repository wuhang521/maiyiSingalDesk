"""Optional local decoder subprocess. No installation, cloud call, stdout or source logging."""
import os
from pathlib import Path
import sys

def main():
    if len(sys.argv) != 3: raise SystemExit(2)
    source, target = map(Path, sys.argv[1:])
    if not source.is_absolute() or source.is_symlink() or not source.is_file() or not 0 < source.stat().st_size <= 20 * 1024 * 1024:
        raise SystemExit(2)
    if not target.is_absolute() or target.exists() or target.suffix != ".wav" or not target.parent.is_dir(): raise SystemExit(2)
    with source.open("rb") as stream: header = stream.read(10)
    if not (header.startswith(b"#!SILK_V3") or header.startswith(b"\x02#!SILK_V3")): raise SystemExit(2)
    if os.name == "posix":
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE, (40 * 1024 * 1024, 40 * 1024 * 1024))
    try:
        import pilk
        pilk.silk_to_wav(str(source), str(target))
        if not 44 < target.stat().st_size <= 40 * 1024 * 1024: raise ValueError()
    except Exception: raise SystemExit(3) from None

if __name__ == "__main__": main()
