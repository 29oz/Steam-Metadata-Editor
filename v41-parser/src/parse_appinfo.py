#!/usr/bin/env python3
"""
parse_appinfo.py – Steam appinfo.vdf parser

Parses the binary appinfo.vdf file (appcache/appinfo.vdf) used by the Steam
client to cache app metadata locally. Supports the current v41 format only
(magic 0x29445607, introduced in the Steam client beta of June 2024).

Usage:
  python parse_appinfo.py [appinfo.vdf] [OPTIONS]

  appinfo.vdf is auto-detected from the default Steam location if not given:
    Windows : %ProgramFiles(x86)%\\Steam\\appcache\\appinfo.vdf
    Linux   : ~/.steam/steam/appcache/appinfo.vdf
    macOS   : ~/Library/Application Support/Steam/appcache/appinfo.vdf

Options:
  --format / -f   json (default) | jsonl | csv
  --output / -o   write to file instead of stdout
  --type   / -t   filter by app type, e.g. --type game  (repeatable)
                  --type game alone also switches to slim game output (see below)
  --date-format   unix (default) | iso
  --pretty        indent JSON output
  --no-empty      skip entries with no name
  --no-raw        omit the raw field from full output

Output modes:
  Default  – all entries, full structure: appid, name, type, change_number,
             last_updated, common, extended, raw
  --type game – games only, slim fields: appid, name, developer, publisher,
             oslist, release_date, metacritic_score, review_score,
             review_percentage, deck_compat (0=Unknown 1=Unsupported
             2=Playable 3=Verified)
"""

import csv
import io
import json
import os
import struct
import sys
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAGIC_V41 = b"\x29\x44\x56\x07"

# VDF type bytes
TYPE_DICT    = 0x00
TYPE_STRING  = 0x01
TYPE_INT32   = 0x02
TYPE_FLOAT32 = 0x03
TYPE_PTR     = 0x04
TYPE_WSTRING = 0x05
TYPE_COLOR   = 0x06
TYPE_UINT64  = 0x07
TYPE_END     = 0x08
TYPE_INT64   = 0x0A

# The fixed per-entry header that follows the `size` field:
# info_state(4) + last_updated(4) + access_token(8) +
# sha1_cdn(20)  + change_number(4) + sha1_data(20)  = 60 bytes
FIXED_HEADER_BYTES = 60


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_timestamp(ts, date_format: str):
    """Return ts as-is for 'unix', or as a YYYY-MM-DD string for 'iso'.
    Passes through empty/None values unchanged."""
    if ts == "" or ts is None:
        return ts
    try:
        ts = int(ts)
    except (ValueError, TypeError):
        return ts
    if date_format == "iso":
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    return ts


def _load_string_table(data: bytes, offset: int) -> list[str]:
    """Read the null-terminated string table at the given offset."""
    count = struct.unpack_from("<I", data, offset)[0]
    pos = offset + 4
    table: list[str] = []
    for _ in range(count):
        end = data.index(b"\x00", pos)
        table.append(data[pos:end].decode("utf-8", errors="replace"))
        pos = end + 1
    return table


# ---------------------------------------------------------------------------
# Binary VDF parser
# ---------------------------------------------------------------------------

def _read_bvdf(data: bytes, pos: int, end: int, st: list[str]) -> tuple[dict, int]:
    """Recursively parse a v41 binary VDF blob.

    Keys are uint32 indices into the string table st.
    Returns (parsed dict, position after the closing TYPE_END byte).
    """
    result: dict[str, Any] = {}
    while pos < end:
        type_byte = data[pos]; pos += 1
        if type_byte == TYPE_END:
            break
        if pos + 4 > end:
            break

        key_idx = struct.unpack_from("<I", data, pos)[0]; pos += 4
        key = st[key_idx] if key_idx < len(st) else f"?{key_idx}"

        if type_byte == TYPE_DICT:
            val, pos = _read_bvdf(data, pos, end, st)
        elif type_byte == TYPE_STRING:
            nul = data.find(b"\x00", pos)
            if nul == -1 or nul > end + 4096:
                break
            val = data[pos:nul].decode("utf-8", errors="replace")
            pos = nul + 1
        elif type_byte == TYPE_INT32:
            val = struct.unpack_from("<i", data, pos)[0]; pos += 4
        elif type_byte == TYPE_FLOAT32:
            val = struct.unpack_from("<f", data, pos)[0]; pos += 4
        elif type_byte in (TYPE_PTR, TYPE_COLOR):
            val = struct.unpack_from("<I", data, pos)[0]; pos += 4
        elif type_byte == TYPE_UINT64:
            val = struct.unpack_from("<Q", data, pos)[0]; pos += 8
        elif type_byte == TYPE_INT64:
            val = struct.unpack_from("<q", data, pos)[0]; pos += 8
        elif type_byte == TYPE_WSTRING:
            nul = data.find(b"\x00\x00", pos)
            if nul == -1:
                break
            val = data[pos:nul].decode("utf-16-le", errors="replace")
            pos = nul + 2
        else:
            # Unknown type – no safe way to skip, bail out of this dict
            return result, end

        result[key] = val
    return result, pos


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def iter_apps(data: bytes) -> Iterator[dict]:
    """Yield one dict per app entry in the raw appinfo.vdf bytes.

    Each dict has: appid, name, type, change_number, last_updated,
    common (dict), extended (dict), raw (dict).

    Raises ValueError if the file magic is not the expected v41 value.
    """
    if data[0:4] != MAGIC_V41:
        raise ValueError(
            f"Unrecognised magic: {data[0:4].hex()} – only v41 (0x29445607) is supported."
        )

    # The string table offset is a uint64 at bytes 8–15 of the file header.
    # All app entries live between byte 16 and this offset.
    st_offset = struct.unpack_from("<Q", data, 8)[0]
    string_table = _load_string_table(data, st_offset)
    data_end = st_offset

    pos = 16  # first app entry starts right after the 16-byte file header
    while pos < data_end - 4:
        appid = struct.unpack_from("<I", data, pos)[0]; pos += 4
        if appid == 0:
            break  # sentinel that marks end of entries

        size          = struct.unpack_from("<I", data, pos)[0]; pos += 4
        _             = struct.unpack_from("<I", data, pos)[0]; pos += 4  # info_state
        last_updated  = struct.unpack_from("<I", data, pos)[0]; pos += 4
        _             = struct.unpack_from("<Q", data, pos)[0]; pos += 8  # access_token
        pos          += 20                                                 # sha1_cdn
        change_number = struct.unpack_from("<I", data, pos)[0]; pos += 4
        pos          += 20                                                 # sha1_data

        vdf_size = size - FIXED_HEADER_BYTES
        vdf_end  = pos + vdf_size
        if vdf_size <= 0 or vdf_end > data_end:
            pos = vdf_end
            continue

        try:
            raw, _ = _read_bvdf(data, pos, vdf_end, string_table)
        except Exception:
            pos = vdf_end
            continue

        appinfo  = raw.get("appinfo", {})
        common   = appinfo.get("common",   {})
        extended = appinfo.get("extended", {})

        yield {
            "appid":         appid,
            "name":          common.get("name", ""),
            "type":          str(common.get("type", "")).lower(),
            "change_number": change_number,
            "last_updated":  last_updated,
            "common":        common,
            "extended":      extended,
            "raw":           raw,
        }
        pos = vdf_end


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

CSV_FIELDS_FULL = [
    "appid", "name", "type", "change_number", "last_updated",
    "oslist", "osarch", "genres", "metacritic_score",
    "store_tags", "categories", "review_score", "review_percentage",
]

CSV_FIELDS_GAME = [
    "appid", "name", "developer", "publisher",
    "oslist", "release_date",
    "metacritic_score", "review_score", "review_percentage",
    "deck_compat",
]


def _to_game_record(app: dict, date_format: str) -> dict:
    """Slim down a full app dict to game-relevant fields only.

    developer/publisher come from extended; everything else from common.
    deck_compat is the integer category inside the steam_deck_compatibility dict.
    """
    common   = app.get("common",   {})
    extended = app.get("extended", {})

    deck_info   = common.get("steam_deck_compatibility", {})
    deck_compat = deck_info.get("category", 0) if isinstance(deck_info, dict) else 0

    return {
        "appid":             app["appid"],
        "name":              app["name"],
        "developer":         extended.get("developer", ""),
        "publisher":         extended.get("publisher", ""),
        "oslist":            common.get("oslist", ""),
        "release_date":      _fmt_timestamp(common.get("steam_release_date", ""), date_format),
        "metacritic_score":  common.get("metacritic_score", ""),
        "review_score":      common.get("review_score", ""),
        "review_percentage": common.get("review_percentage", ""),
        "deck_compat":       deck_compat,
    }


def _to_flat_full(app: dict, date_format: str) -> dict:
    """Flatten a full app dict to a single-level dict for CSV output."""
    common = app.get("common", {})
    return {
        "appid":             app["appid"],
        "name":              app["name"],
        "type":              app["type"],
        "change_number":     app["change_number"],
        "last_updated":      _fmt_timestamp(app["last_updated"], date_format),
        "oslist":            common.get("oslist", ""),
        "osarch":            common.get("osarch", ""),
        "genres":            json.dumps(common.get("genres", {})),
        "metacritic_score":  common.get("metacritic_score", ""),
        "store_tags":        json.dumps(common.get("store_tags", {})),
        "categories":        json.dumps(common.get("category", {})),
        "review_score":      common.get("review_score", ""),
        "review_percentage": common.get("review_percentage", ""),
    }


def write_output(apps: list[dict], fmt: str, pretty: bool, out, game_mode: bool, date_format: str):
    if fmt == "json":
        json.dump(apps, out, ensure_ascii=False, indent=2 if pretty else None)
        out.write("\n")
    elif fmt == "jsonl":
        for app in apps:
            json.dump(app, out, ensure_ascii=False)
            out.write("\n")
    elif fmt == "csv":
        if game_mode:
            fields, rows = CSV_FIELDS_GAME, apps
        else:
            fields = CSV_FIELDS_FULL
            rows   = [_to_flat_full(a, date_format) for a in apps]
        writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def default_appinfo_path() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        return Path(base) / "Steam" / "appcache" / "appinfo.vdf"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Steam" / "appcache" / "appinfo.vdf"
    return Path.home() / ".steam" / "steam" / "appcache" / "appinfo.vdf"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Parse Steam's binary appinfo.vdf (v41) into JSON / JSONL / CSV.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("vdf_path", nargs="?", metavar="appinfo.vdf",
                   help="path to appinfo.vdf (default: auto-detect)")
    p.add_argument("--format", "-f", choices=["json", "jsonl", "csv"], default="json",
                   help="output format (default: json)")
    p.add_argument("--output", "-o", metavar="PATH",
                   help="write to file instead of stdout")
    p.add_argument("--type", "-t", action="append", dest="types", metavar="TYPE",
                   help="filter by app type, e.g. --type game (repeatable; "
                        "--type game alone enables slim game output)")
    p.add_argument("--date-format", "-d", choices=["unix", "iso"], default="unix",
                   dest="date_format",
                   help="timestamp format: unix integer (default) or YYYY-MM-DD")
    p.add_argument("--pretty", action="store_true", help="indent JSON output")
    p.add_argument("--no-empty", action="store_true", help="skip entries with no name")
    p.add_argument("--no-raw", action="store_true",
                   help="omit the raw field from full output")
    return p


def main():
    args = build_parser().parse_args()

    vdf_path = Path(args.vdf_path) if args.vdf_path else default_appinfo_path()
    if not vdf_path.exists():
        print(f"error: file not found: {vdf_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Reading {vdf_path} ({vdf_path.stat().st_size:,} bytes) …", file=sys.stderr)
    data = vdf_path.read_bytes()

    allowed_types = {t.lower() for t in args.types} if args.types else None
    game_mode     = (allowed_types == {"game"})

    apps = []
    for app in iter_apps(data):
        if args.no_empty and not app["name"]:
            continue
        if allowed_types and app["type"] not in allowed_types:
            continue
        if args.no_raw:
            app.pop("raw", None)
        apps.append(_to_game_record(app, args.date_format) if game_mode else app)

    print(f"Found {len(apps):,} matching entries.", file=sys.stderr)

    if args.output:
        out_path = Path(args.output)
        with out_path.open("w", encoding="utf-8", newline="") as f:
            write_output(apps, args.format, args.pretty, f, game_mode, args.date_format)
        print(f"Written to {out_path}", file=sys.stderr)
    else:
        out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="")
        write_output(apps, args.format, args.pretty, out, game_mode, args.date_format)
        out.detach()


if __name__ == "__main__":
    main()
