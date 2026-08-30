"""Pure-Python re-implementation of `zipalign -p -f <alignment>`.

Android's package installer and the runtime asset-bundle loader mmap STORED
(uncompressed) zip entries directly, which requires each such entry's DATA to
start at an aligned file offset -- normally a multiple of 4 bytes, and for
STORED native libraries specifically, a multiple of the device page size
(16384, to support 16KB-page devices; also satisfies the older 4096
requirement since 16384 is a multiple of it).

Zipalign achieves this purely by adjusting the local file header's "extra
field" padding for each entry, without touching any entry's compressed
bytes. This module does the same: every entry's raw payload is copied
byte-for-byte from the source archive (whatever its compression method), and
only the padding inserted before it changes. Confirmed against a real
`zipalign`-built APK: the amount of padding is what matters, not any
particular "extra field" sub-structure -- so this simply pads with zero
bytes, and `zipalign -c` (alignment-only verification) accepts that.
"""

from __future__ import annotations

import struct
import zipfile

LOCAL_HEADER_FIXED_SIZE = 30      # bytes before name+extra in a local file header
CENTRAL_HEADER_FIXED_SIZE = 46    # bytes before name+extra+comment in a central dir entry
CENTRAL_HEADER_STRUCT = "<4sHHHHHHIIIHHHHHII"
EOCD_SIG = b"PK\x05\x06"


def _find_eocd_offset(data: bytes) -> int:
    # The EOCD is 22 bytes plus up to 65535 bytes of a trailing comment; search backward.
    tail_start = max(0, len(data) - 22 - 65535)
    idx = data.rfind(EOCD_SIG, tail_start)
    if idx < 0:
        raise ValueError("not a valid zip: no End Of Central Directory record found")
    return idx


def _read_central_records(data: bytes):
    """Parse the ORIGINAL central directory sequentially (record sizes are
    variable, so each entry's start can only be found by walking from the
    previous one) -> a list of raw field tuples in on-disk order, matching
    the order zipfile.ZipFile.infolist() reports."""
    eocd_off = _find_eocd_offset(data)
    (_sig, _disk, _cd_disk, _n_disk, n_total, cd_size, cd_offset,
     _comment_len) = struct.unpack_from("<4sHHHHIIH", data, eocd_off)
    records = []
    off = cd_offset
    for _ in range(n_total):
        fields = struct.unpack_from(CENTRAL_HEADER_STRUCT, data, off)
        (_sig, _vmade, _vext, _flags, _method, _mtime, _mdate, _crc, _csize,
         _usize, nlen, elen, clen, _disk_no, _int_attr, _ext_attr, _lho) = fields
        name = data[off + CENTRAL_HEADER_FIXED_SIZE: off + CENTRAL_HEADER_FIXED_SIZE + nlen]
        extra = data[off + CENTRAL_HEADER_FIXED_SIZE + nlen:
                    off + CENTRAL_HEADER_FIXED_SIZE + nlen + elen]
        comment = data[off + CENTRAL_HEADER_FIXED_SIZE + nlen + elen:
                      off + CENTRAL_HEADER_FIXED_SIZE + nlen + elen + clen]
        records.append({"fields": fields, "name": name, "extra": extra, "comment": comment})
        off += CENTRAL_HEADER_FIXED_SIZE + nlen + elen + clen
    return records


def _needed_padding(offset: int, alignment: int) -> int:
    return (-offset) % alignment


def zipalign_bytes(apk_bytes: bytes, alignment: int = 4, so_alignment: int = 16384) -> bytes:
    """Return a re-aligned copy of an APK's bytes, preserving every entry's
    original compressed payload, compression method, and metadata exactly."""
    central_records = _read_central_records(apk_bytes)

    out = bytearray()
    new_local_offsets = []

    for rec in central_records:
        (_sig, _vmade, _vext, _flags, method, mtime, mdate, crc, csize, usize,
         nlen, _elen, _clen, _disk_no, _int_attr, _ext_attr, local_header_offset) = rec["fields"]

        # Re-read the LOCAL header for this entry (flags/method here are authoritative
        # for how the payload was actually written; central and local copies agree in
        # practice, but source from the local header since that's what a reader uses).
        (l_sig, l_ver_extract, l_flags, l_method, l_mtime, l_mdate, l_crc, l_csize,
         l_usize, l_nlen, l_elen) = struct.unpack_from("<4sHHHHHIIIHH", apk_bytes,
                                                        local_header_offset)
        name_bytes = apk_bytes[local_header_offset + LOCAL_HEADER_FIXED_SIZE:
                              local_header_offset + LOCAL_HEADER_FIXED_SIZE + l_nlen]
        data_start = local_header_offset + LOCAL_HEADER_FIXED_SIZE + l_nlen + l_elen
        payload = apk_bytes[data_start:data_start + l_csize]

        is_stored = (l_method == zipfile.ZIP_STORED)
        if is_stored:
            want_align = so_alignment if name_bytes.endswith(b".so") else alignment
        else:
            want_align = 1

        new_local_header_offset = len(out)
        if want_align > 1:
            data_would_start = new_local_header_offset + LOCAL_HEADER_FIXED_SIZE + l_nlen
            pad = _needed_padding(data_would_start, want_align)
        else:
            pad = 0

        out += struct.pack("<4sHHHHHIIIHH", l_sig, l_ver_extract, l_flags, l_method,
                           l_mtime, l_mdate, l_crc, l_csize, l_usize, l_nlen, pad)
        out += name_bytes
        out += b"\x00" * pad
        out += payload

        new_local_offsets.append(new_local_header_offset)

    central_dir_offset = len(out)
    for rec, new_local_header_offset in zip(central_records, new_local_offsets):
        (sig, vmade, vext, flags, method, mtime, mdate, crc, csize, usize,
         nlen, elen, clen, disk_no, int_attr, ext_attr, _old_lho) = rec["fields"]
        out += struct.pack(CENTRAL_HEADER_STRUCT, sig, vmade, vext, flags, method,
                           mtime, mdate, crc, csize, usize, nlen, elen, clen,
                           disk_no, int_attr, ext_attr, new_local_header_offset)
        out += rec["name"]
        out += rec["extra"]
        out += rec["comment"]

    central_dir_size = len(out) - central_dir_offset
    n_entries = len(central_records)
    out += struct.pack("<4sHHHHIIH", EOCD_SIG, 0, 0, n_entries, n_entries,
                       central_dir_size, central_dir_offset, 0)
    return bytes(out)
