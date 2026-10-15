"""Disassemble the DX11 programs of a client shader (to read AQW:I's colour-mask maths).

usage: python dump_shader_dxbc.py <Unity _Data dir> <shader name> <out dir>
Finds the Shader object by parsed name, LZ4-decompresses its compressed blobs, carves every 'DXBC' container
and disassembles it with the system d3dcompiler_47.dll (D3DDisassemble).
"""
import ctypes
import os
import struct
import sys

import UnityPy

data_dir, wanted, out = sys.argv[1:4]
os.makedirs(out, exist_ok=True)
env = UnityPy.load(data_dir) if os.path.isfile(data_dir) else UnityPy.load(*[
    os.path.join(data_dir, f) for f in ("resources.assets", "sharedassets0.assets", "globalgamemanagers.assets")])
d3d = ctypes.WinDLL("d3dcompiler_47.dll")
D3DDisassemble = d3d.D3DDisassemble
D3DDisassemble.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]


class ID3DBlobVtbl(ctypes.Structure):
    _fields_ = [("QueryInterface", ctypes.c_void_p), ("AddRef", ctypes.c_void_p), ("Release", ctypes.c_void_p),
                ("GetBufferPointer", ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)),
                ("GetBufferSize", ctypes.WINFUNCTYPE(ctypes.c_size_t, ctypes.c_void_p))]


def disasm(dxbc: bytes) -> str:
    blob = ctypes.c_void_p()
    buf = ctypes.create_string_buffer(dxbc, len(dxbc))
    hr = D3DDisassemble(buf, len(dxbc), 0, None, ctypes.byref(blob))
    if hr != 0:
        return "D3DDisassemble failed 0x%08x" % (hr & 0xFFFFFFFF)
    vt = ctypes.cast(ctypes.cast(blob, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ID3DBlobVtbl)).contents
    ptr, size = vt.GetBufferPointer(blob), vt.GetBufferSize(blob)
    return ctypes.string_at(ptr, size).decode("latin-1")


def lz4_block(src: bytes, size: int) -> bytes:
    dst, i = bytearray(), 0
    while i < len(src) and len(dst) < size:
        token = src[i]; i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]; i += 1; lit += b
                if b != 255:
                    break
        dst += src[i:i + lit]; i += lit
        if i >= len(src):
            break
        off = src[i] | (src[i + 1] << 8); i += 2
        ml = token & 15
        if ml == 15:
            while True:
                b = src[i]; i += 1; ml += b
                if b != 255:
                    break
        ml += 4
        start = len(dst) - off
        for k in range(ml):
            dst.append(dst[start + k])
    return bytes(dst)


for o in env.objects:
    if o.type.name != "Shader":
        continue
    s = o.read()
    name = s.m_ParsedForm.m_Name if getattr(s, "m_ParsedForm", None) else ""
    if name != wanted:
        continue
    blob = bytes(s.compressedBlob)
    offsets, csizes, dsizes = s.offsets, s.compressedLengths, s.decompressedLengths
    n = 0
    for plat in range(len(offsets)):
        offs = offsets[plat] if isinstance(offsets[plat], list) else [offsets[plat]]
        cls = csizes[plat] if isinstance(csizes[plat], list) else [csizes[plat]]
        dls = dsizes[plat] if isinstance(dsizes[plat], list) else [dsizes[plat]]
        for off, cl, dl in zip(offs, cls, dls):
            raw = lz4_block(blob[off:off + cl], dl)
            p = 0
            while True:
                p = raw.find(b"DXBC", p)
                if p < 0:
                    break
                size = struct.unpack_from("<I", raw, p + 24)[0]
                text = disasm(raw[p:p + size])
                kind = "ps" if "ps_5" in text or "ps_4" in text else ("vs" if "vs_" in text else "other")
                path = os.path.join(out, "%s_%02d_%s.asm" % (wanted.replace("/", "_").replace(" ", "_"), n, kind))
                open(path, "w").write(text)
                n += 1
                p += max(size, 4)
    print(wanted, "programs:", n)
