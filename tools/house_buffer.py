#!/usr/bin/env python3
"""Preview/apply the reversible HOUSE 3-second Snapserver buffer experiment."""
import argparse
import difflib
import os
from pathlib import Path
import re
import shlex
import stat
import tempfile
import time


def update_buffer(text: str, milliseconds: int) -> str:
    if not 1000 <= milliseconds <= 5000:
        raise ValueError("Experiment range is 1000–5000 ms; start with 3000.")
    lines = text.splitlines(keepends=True)
    sections = [(i, m.group(1)) for i, line in enumerate(lines)
                if (m := re.fullmatch(r"\s*\[([^]]+)\]\s*(?:[#;].*)?\r?\n?", line))]
    starts = [i for i, name in sections if name == "stream"]
    if len(starts) != 1:
        raise ValueError("Expected exactly one [stream] section; review the config manually.")
    start = starts[0]
    end = next((i for i, _ in sections if i > start), len(lines))
    keys = [i for i in range(start + 1, end) if re.match(r"\s*buffer\s*=", lines[i])]
    if len(keys) > 1:
        raise ValueError("Duplicate active stream buffer settings; review manually.")
    # Keep the small chunk cadence unchanged. Refuse a different/ambiguous cadence.
    for line in lines[start + 1:end]:
        if not line.strip() or line.lstrip().startswith(("#", ";")):
            continue
        chunk = re.search(r"(?:^\s*chunk_ms\s*=\s*|[?&]chunk_ms=)(\d+)", line)
        if chunk and int(chunk.group(1)) != 20:
            raise ValueError("This trial expects 20 ms chunks; review the source configuration.")
    newline = "\r\n" if "\r\n" in text else "\n"
    if keys:
        i = keys[0]
        match = re.fullmatch(r"(\s*buffer\s*=\s*)\d+(\s*(?:[#;].*)?)\r?\n?", lines[i].rstrip("\r\n"))
        if not match:
            raise ValueError("Unrecognized buffer setting; review manually.")
        ending = newline if lines[i].endswith("\n") else ""
        lines[i] = f"{match.group(1)}{milliseconds}{match.group(2)}{ending}"
    else:
        if not lines[start].endswith("\n"):
            lines[start] += newline
        lines.insert(start + 1, f"buffer = {milliseconds}{newline}")
    return "".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/etc/snapserver.conf"))
    parser.add_argument("--buffer-ms", type=int, default=3000)
    parser.add_argument("--apply", action="store_true", help="Back up and write config; restart remains explicit")
    args = parser.parse_args()
    path = args.config
    if path.is_symlink() or not path.is_file():
        parser.error("Config must be a regular, non-symlink file.")
    before = path.read_bytes()
    try:
        old = before.decode("utf-8")
        new = update_buffer(old, args.buffer_ms)
    except (ValueError, UnicodeError) as error:
        parser.error(str(error))
    if new == old:
        print("Configuration already matches; no files changed. Verify the running server separately.")
        return
    print("".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                     fromfile=str(path), tofile=str(path) + " (proposed)")), end="")
    if not args.apply:
        print("\nPreview only. Repeat with --apply after reviewing the diff.")
        return
    metadata = path.stat()
    backup = path.with_name(f"{path.name}.house-buffer-{time.time_ns()}.bak")
    # Exclusive creation; never replace an existing backup.
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IMODE(metadata.st_mode))
    with os.fdopen(fd, "wb") as saved:
        saved.write(before)
        saved.flush()
        os.fsync(saved.fileno())
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(new.encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), stat.S_IMODE(metadata.st_mode))
            if os.geteuid() == 0:
                os.fchown(output.fileno(), metadata.st_uid, metadata.st_gid)
        if path.is_symlink() or path.read_bytes() != before:
            raise RuntimeError("Configuration changed during preparation; refusing to overwrite it.")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"\nSaved config. Backup: {backup}")
    print("Activate (briefly interrupts all house outputs): sudo systemctl restart snapserver")
    print("Rollback: sudo cp -- " + shlex.quote(str(backup)) + " " + shlex.quote(str(path)))
    print("Then: sudo systemctl restart snapserver")


if __name__ == "__main__":
    main()
