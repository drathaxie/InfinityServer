#!/usr/bin/env python3
"""Build an arm64 Android AQ2D client that uses the InfinityServer Web API.

This is intentionally a version-pinned binary patcher.  It validates every
input marker it relies on before changing an APK, instead of attempting to
patch an unknown AQ2D build.  The output remains dependent on the original
AQ2D assets; it changes only the app's Web API endpoint selection.
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
import zipfile
from pathlib import Path


ARM64_LIBRARY = "lib/arm64-v8a/libil2cpp.so"
ARMV7_PREFIX = "lib/armeabi-v7a/"
METADATA_PATH = "assets/bin/Data/Managed/Metadata/global-metadata.dat"

SERVER_URL = b"https://divinityarts.mooo.com/"
EXPECTED_SOURCE_SHA256 = "e0a68f06429a31d9c8a5f7d9a66015376a9e42c7b3afd932863f91fadcccb0ab"
# Searched BY CONTENT (not a hardcoded table index): AQ2D 0.0.254 proved the literal table can
# reorder between builds even when this exact diagnostic string survives unchanged. Scanning for
# it is the only part of this patch that stayed genuinely build-independent across that update.
DONOR_LITERAL = b"Context set, calling callback."

# Main.get_WebApiURL in AQ2D 0.0.253 (Unity 6000.3.17f1, arm64-v8a).
WEB_API_CACHE_VADDR = 0x049A2E18
WEB_API_CACHE_ORIGINAL = 0xA0008A89
WEB_API_CODE_DESTINATIONS = (
    0x021114A4,
    0x021114A8,
    0x021114AC,
    0x021114B0,
    0x021114B4,
    0x021114B8,
)
WEB_API_CODE_SOURCES = (
    0x021114EC,  # ADRP X8, WebApiURL cache page
    0x021114F0,  # LDR X8, [X8, WebApiURL cache offset]
    0x021114F8,  # LDR X0, [X8]
    0x021114F4,  # Restore X20/X19
    0x021114FC,  # Restore X30/X21 and stack
    0x02111500,  # RET
)


def elf_virtual_to_file_offset(image: bytes, vaddr: int) -> int:
    """Map a virtual address in a 64-bit little-endian ELF to a file offset."""

    if image[:4] != b"\x7fELF" or image[4] != 2 or image[5] != 1:
        raise ValueError("expected a 64-bit little-endian ELF library")

    header = struct.unpack_from("<16sHHIQQQIHHHHHH", image, 0)
    program_header_offset = header[5]
    program_header_entry_size = header[9]
    program_header_count = header[10]

    for index in range(program_header_count):
        offset = program_header_offset + index * program_header_entry_size
        p_type, _, p_offset, p_vaddr, _, p_filesz, _, _ = struct.unpack_from(
            "<IIQQQQQQ", image, offset
        )
        if p_type == 1 and p_vaddr <= vaddr < p_vaddr + p_filesz:
            return p_offset + vaddr - p_vaddr
    raise ValueError(f"virtual address 0x{vaddr:X} is not backed by the ELF file")


def metadata_literal_offsets(metadata: bytes) -> tuple[list[int], int, int]:
    """Return string literal starts plus the bounds of their data section."""

    magic, version = struct.unpack_from("<II", metadata, 0)
    if magic != 0xFAB11BAF or version != 39:
        raise ValueError("expected AQ2D global-metadata.dat version 39")

    literal_offset, literal_size, literal_count = struct.unpack_from("<III", metadata, 8)
    data_offset, data_size, _ = struct.unpack_from("<III", metadata, 20)
    if literal_size != literal_count * 4:
        raise ValueError("unexpected version-39 string literal table layout")
    if data_offset + data_size > len(metadata):
        raise ValueError("string literal data is outside global-metadata.dat")

    literal_starts = [
        struct.unpack_from("<I", metadata, literal_offset + index * 4)[0]
        for index in range(literal_count)
    ]
    if literal_starts != sorted(literal_starts) or literal_starts[-1] > data_size:
        raise ValueError("string literal table is malformed")
    return literal_starts, data_offset, data_size


def find_donor_literal(metadata: bytes) -> tuple[int, int, int]:
    """Locate the donor literal BY CONTENT, not by a hardcoded table index -> (index, start, end).
    The literal table can reorder between builds (AQ2D 0.0.254 shifted the surrounding string
    data even though this exact diagnostic string survived), so pinning an index is not safe
    across an update; scanning for the bytes is. Requires exactly one match -- an ambiguous
    donor is refused rather than guessed at."""
    starts, data_offset, data_size = metadata_literal_offsets(metadata)
    hits = []
    for i in range(len(starts) - 1):
        start = data_offset + starts[i]
        end = data_offset + starts[i + 1]
        if end <= data_offset + data_size and metadata[start:end] == DONOR_LITERAL:
            hits.append((i, start, end))
    if not hits:
        raise ValueError("the donor literal is not present in this AQ2D build's metadata")
    if len(hits) > 1:
        raise ValueError(
            f"the donor literal is ambiguous in this build ({len(hits)} matches) -- refusing to guess")
    return hits[0]


def patch_metadata(metadata: bytes) -> tuple[bytes, int]:
    """Replace an equal-length diagnostic literal with the server base URL -> (patched, index).
    The index is handed to patch_arm64_library so the cache token it writes points at wherever
    the donor literal actually landed in THIS build, instead of a build-specific constant."""

    if len(SERVER_URL) != len(DONOR_LITERAL):
        raise ValueError("the pinned URL donor slot is not the required length")
    index, start, end = find_donor_literal(metadata)

    patched = bytearray(metadata)
    patched[start:end] = SERVER_URL
    return bytes(patched), index


def patch_arm64_library(library: bytes, donor_index: int) -> bytes:
    """Force Main.get_WebApiURL to return the initialized Web API cache."""

    cache_offset = elf_virtual_to_file_offset(library, WEB_API_CACHE_VADDR)
    current_token = struct.unpack_from("<I", library, cache_offset)[0]
    if current_token != WEB_API_CACHE_ORIGINAL:
        raise ValueError(
            "the WebApiURL cache token does not match AQ2D 0.0.253 "
            f"(got 0x{current_token:08X})"
        )

    source_words = [
        library[elf_virtual_to_file_offset(library, source) : elf_virtual_to_file_offset(library, source) + 4]
        for source in WEB_API_CODE_SOURCES
    ]
    patched = bytearray(library)
    for destination, word in zip(WEB_API_CODE_DESTINATIONS, source_words, strict=True):
        offset = elf_virtual_to_file_offset(library, destination)
        patched[offset : offset + 4] = word

    donor_token = 0xA0000000 | (donor_index << 1) | 1
    struct.pack_into("<I", patched, cache_offset, donor_token)
    return bytes(patched)


def verify_patches(library: bytes, metadata: bytes, donor_index: int) -> None:
    starts, data_offset, _ = metadata_literal_offsets(metadata)
    literal = metadata[
        data_offset + starts[donor_index] : data_offset + starts[donor_index + 1]
    ]
    if literal != SERVER_URL:
        raise ValueError("patched metadata does not contain the requested Web API URL")

    cache_offset = elf_virtual_to_file_offset(library, WEB_API_CACHE_VADDR)
    expected_token = 0xA0000000 | (donor_index << 1) | 1
    if struct.unpack_from("<I", library, cache_offset)[0] != expected_token:
        raise ValueError("patched WebApiURL cache token is not valid")

    for destination, source in zip(WEB_API_CODE_DESTINATIONS, WEB_API_CODE_SOURCES, strict=True):
        destination_offset = elf_virtual_to_file_offset(library, destination)
        source_offset = elf_virtual_to_file_offset(library, source)
        if library[destination_offset : destination_offset + 4] != library[source_offset : source_offset + 4]:
            raise ValueError("patched Main.get_WebApiURL return sequence is incomplete")


def clone_zip_info(source: zipfile.ZipInfo) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(source.filename, date_time=source.date_time)
    info.compress_type = source.compress_type
    info.comment = source.comment
    info.extra = source.extra
    info.internal_attr = source.internal_attr
    info.external_attr = source.external_attr
    info.create_system = source.create_system
    info.create_version = source.create_version
    info.extract_version = source.extract_version
    return info


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(source_apk: Path, output_apk: Path) -> None:
    source_hash = sha256_file(source_apk)
    if source_hash != EXPECTED_SOURCE_SHA256:
        raise ValueError(
            "source APK fingerprint is not the supported AQ2D 0.0.253 build "
            f"(got {source_hash})"
        )

    with zipfile.ZipFile(source_apk, "r") as source:
        names = set(source.namelist())
        if ARM64_LIBRARY not in names or METADATA_PATH not in names:
            raise ValueError("source APK does not contain the expected AQ2D arm64 runtime files")

        patched_metadata, donor_index = patch_metadata(source.read(METADATA_PATH))
        patched_library = patch_arm64_library(source.read(ARM64_LIBRARY), donor_index)
        verify_patches(patched_library, patched_metadata, donor_index)

        output_apk.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output_apk, "w", allowZip64=True) as output:
            for source_info in source.infolist():
                if source_info.filename.startswith(ARMV7_PREFIX):
                    continue
                payload = source.read(source_info.filename)
                if source_info.filename == ARM64_LIBRARY:
                    payload = patched_library
                elif source_info.filename == METADATA_PATH:
                    payload = patched_metadata
                output.writestr(clone_zip_info(source_info), payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_apk", type=Path, help="original aq2d.apk (version 0.0.253)")
    parser.add_argument("output_apk", type=Path, help="unsigned arm64-only patched APK")
    args = parser.parse_args()

    try:
        build(args.source_apk, args.output_apk)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"AQ2D Android port failed: {error}", file=sys.stderr)
        return 1

    print(f"Created unsigned arm64-only port: {args.output_apk}")
    print(f"Web API base URL: {SERVER_URL.decode('ascii')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
