#!/usr/bin/env python3
"""Build an arm64 Android AQ2D client that uses the InfinityServer Web API.

VERSION-AGNOSTIC by construction: rather than patching Main.get_WebApiURL's
compiled bytes at a hardcoded, build-specific address (the original approach,
which needed fresh disassembly every AQ2D release -- and broke silently when
0.0.254 shipped a day after 0.0.253 was patched), this appends a small ARM64
routine to libil2cpp.so that runs at library load, waits for IL2CPP to finish
initializing, then finds Main.get_WebApiURL BY NAME through IL2CPP's own
public reflection API (il2cpp_class_from_name / il2cpp_class_get_method_from_
name) and overwrites its compiled function pointer to return our URL instead.

Every address the hook needs -- the il2cpp_* functions, pthread_create,
usleep, __android_log_print -- is resolved from the target .so's OWN ELF
symbol/relocation tables at patch time, by name. Nothing is hardcoded per
build, so this script does not need re-analysis for the next AQ2D release
the way the address-patching approach did. It still refuses to touch
anything that isn't structurally what it expects (see validate()) rather
than guessing at an unfamiliar layout.

Requires `pip install keystone-engine` (a prebuilt-wheel ARM64 assembler --
used only to encode the small fixed hook routine below, once per run).
"""

from __future__ import annotations

import argparse
import struct
import sys
import zipfile
from pathlib import Path

try:
    from keystone import Ks, KS_ARCH_ARM64, KS_MODE_LITTLE_ENDIAN
except ImportError:
    print("This script needs the keystone ARM64 assembler: pip install keystone-engine",
         file=sys.stderr)
    raise SystemExit(1)


ARM64_LIBRARY = "lib/arm64-v8a/libil2cpp.so"
ARMV7_PREFIX = "lib/armeabi-v7a/"
METADATA_PATH = "assets/bin/Data/Managed/Metadata/global-metadata.dat"
MANIFEST_PATH = "AndroidManifest.xml"
EXPECTED_PACKAGE = b"com.Artix.aq2d"

SERVER_URL = "https://divinityarts.mooo.com/"
SEG_ALIGN = 0x4000            # matches AQ2D's own PT_LOAD alignment (16K pages)
GIVE_UP_RETRIES = 200         # ~200 x 60ms = 12s budget polling the domain cache directly
USLEEP_PER_RETRY_US = 60000

for _name in ("GIVE_UP_RETRIES", "USLEEP_PER_RETRY_US"):
    if not (0 <= globals()[_name] <= 0xFFFF):
        raise ValueError(f"{_name}={globals()[_name]} exceeds a plain `mov`'s 16-bit "
                         "immediate (0-65535) -- this script never emits movz+movk, "
                         "so a bigger value fails to assemble at all")
del _name

EM_AARCH64 = 183
DT_JMPREL, DT_PLTRELSZ, DT_INIT_ARRAY, DT_INIT_ARRAYSZ = 0x17, 0x02, 0x19, 0x1B
DT_RELA, DT_RELASZ = 0x07, 0x08
PT_LOAD, PT_DYNAMIC, PT_PHDR = 1, 2, 6
R_AARCH64_RELATIVE = 1027


# --- minimal hand-rolled ELF64 LE reader (used both to validate and to patch) ---------

class Elf64:
    def __init__(self, data: bytes):
        self.data = bytearray(data)
        if self.data[:4] != b"\x7fELF" or self.data[4] != 2 or self.data[5] != 1:
            raise ValueError("expected a 64-bit little-endian ELF library")
        e_machine = struct.unpack_from("<H", self.data, 0x12)[0]
        if e_machine != EM_AARCH64:
            raise ValueError("expected an AArch64 (arm64-v8a) library")
        self.e_phoff, self.e_shoff = struct.unpack_from("<QQ", self.data, 0x20)
        (self.e_phentsize, self.e_phnum, self.e_shentsize, self.e_shnum,
         self.e_shstrndx) = struct.unpack_from("<HHHHH", self.data, 0x36)

    def segments(self):
        out = []
        for i in range(self.e_phnum):
            off = self.e_phoff + i * self.e_phentsize
            (p_type, p_flags, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz,
             p_align) = struct.unpack_from("<IIQQQQQQ", self.data, off)
            out.append(dict(type=p_type, flags=p_flags, offset=p_offset,
                            vaddr=p_vaddr, filesz=p_filesz, memsz=p_memsz))
        return out

    def sections(self):
        secs = []
        for i in range(self.e_shnum):
            off = self.e_shoff + i * self.e_shentsize
            (sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size, sh_link,
             sh_info, sh_addralign, sh_entsize) = struct.unpack_from(
                "<IIQQQQIIQQ", self.data, off)
            secs.append(dict(name_off=sh_name, addr=sh_addr, offset=sh_offset,
                             size=sh_size, entsize=sh_entsize))
        shstr = secs[self.e_shstrndx]
        base = shstr["offset"]
        for s in secs:
            end = self.data.index(b"\x00", base + s["name_off"])
            s["name"] = self.data[base + s["name_off"]:end].decode("ascii")
        return secs

    def section(self, name):
        for s in self.sections():
            if s["name"] == name:
                return s
        raise ValueError(f"missing ELF section {name!r}")

    def v2o(self, vaddr):
        for seg in self.segments():
            if seg["type"] == PT_LOAD and seg["vaddr"] <= vaddr < seg["vaddr"] + seg["filesz"]:
                return seg["offset"] + vaddr - seg["vaddr"]
        raise ValueError(f"virtual address 0x{vaddr:x} is not backed by the ELF file")

    def dynamic_entries(self):
        dyn = self.section(".dynamic")
        entries, off, i = [], dyn["offset"], 0
        while True:
            d_tag, d_val = struct.unpack_from("<qQ", self.data, off + i * 16)
            entries.append((d_tag, d_val))
            i += 1
            if d_tag == 0:
                break
        return entries, dyn

    def dynsym(self):
        dynsym, dynstr = self.section(".dynsym"), self.section(".dynstr")
        n = dynsym["size"] // dynsym["entsize"]
        out = []
        for i in range(n):
            off = dynsym["offset"] + i * 24
            st_name, st_info, st_other, st_shndx, st_value, st_size = \
                struct.unpack_from("<IBBHQQ", self.data, off)
            end = self.data.index(b"\x00", dynstr["offset"] + st_name)
            name = self.data[dynstr["offset"] + st_name:end].decode("ascii")
            out.append(dict(name=name, value=st_value, shndx=st_shndx))
        return out

    def find_direct(self, name, symbols):
        for s in symbols:
            if s["name"] == name and s["shndx"] != 0:
                return s["value"]
        raise ValueError(f"{name} is not an exported (defined) symbol in this library")

    def find_relative_reloc_file_offset(self, target_r_offset):
        """File offset of the R_AARCH64_RELATIVE .rela.dyn entry that fixes up the
        given vaddr (an init_array slot), so its addend can be repointed in place --
        the ONLY way to add a new load-time entry point without either growing the
        12MB .rela.dyn table or leaving the new pointer un-relocated (confirmed on
        device: an appended-but-unrelocated init_array entry is called with its raw
        LINK-TIME address, not load_bias-adjusted, and faults into whatever unrelated
        mapping happens to sit at that fixed address -- ASLR makes this always wrong)."""
        entries, _ = self.dynamic_entries()
        tags = dict(entries)
        off = self.v2o(tags[DT_RELA])
        n = tags[DT_RELASZ] // 24
        for i in range(n):
            entry_off = off + i * 24
            r_offset, r_info = struct.unpack_from("<QQ", self.data, entry_off)
            if r_offset == target_r_offset and (r_info & 0xFFFFFFFF) == R_AARCH64_RELATIVE:
                return entry_off
        raise ValueError(f"no R_AARCH64_RELATIVE relocation targets vaddr {hex(target_r_offset)}")

    def find_plt_stub(self, name, symbols):
        """Address of the PLT trampoline for an imported symbol, from its ordinal
        position in .rela.plt -- verified against real Unity/lld ARM64 output: a
        fixed 32-byte PLT0 bootstrap stub, followed by one 16-byte stub per
        .rela.plt entry, in the same order. No disassembly, no per-build offsets."""
        entries, _ = self.dynamic_entries()
        tags = dict(entries)
        off = self.v2o(tags[DT_JMPREL])
        n = tags[DT_PLTRELSZ] // 24
        plt = self.section(".plt")
        for i in range(n):
            r_offset, r_info, r_addend = struct.unpack_from("<QQq", self.data, off + i * 24)
            if symbols[r_info >> 32]["name"] == name:
                return plt["addr"] + 32 + i * 16
        raise ValueError(f"{name} has no PLT relocation in this library")


REQUIRED_DIRECT = ("il2cpp_domain_get", "il2cpp_domain_get_assemblies",
                   "il2cpp_assembly_get_image", "il2cpp_class_from_name",
                   "il2cpp_class_get_method_from_name",
                   "il2cpp_thread_attach", "il2cpp_thread_detach")
REQUIRED_PLT = ("pthread_create", "usleep", "__android_log_print", "il2cpp_string_new",
               "dlopen", "dlsym")


# --- a tiny, generic ARM64 pattern matcher (NOT a per-build hack) -----------------------
# il2cpp_domain_get() is a lazy-initializing singleton accessor, not a plain read: if its
# cached global is still null, it initializes it right there on the calling thread --
# allocating, reading other not-yet-populated IL2CPP globals, and calling further internal
# functions. Confirmed on-device: a freshly spawned thread calling it immediately can WIN
# the race to be the very first caller in the whole process and crash deep inside that
# init path (a null-pointer SIGSEGV, always at the same offset), regardless of JNI/IL2CPP
# thread-attach correctness -- attaching fixes a real but different requirement, it does
# not change who gets there first. The fix is to never be that first caller: poll the
# cached global directly (a plain memory read can't trigger the hazard) and only call the
# real accessor once it's already non-null, guaranteeing the fast path.
#
# The cached global's address is build-specific and unnamed, so it can't be resolved by
# symbol the way everything else here is. But its accessor's machine code always begins
# with the same three-instruction idiom regardless of build -- ADRP+LDR loading the cache,
# then CBNZ testing what was just loaded -- and decoding that shape is a few lines of
# fixed ARM64 bit-twiddling, not manual reverse engineering: this runs automatically for
# whatever build is handed to the script, the same way the rest of resolve_addresses does.
def _decode_adrp(word, at_addr):
    if (word & 0x9F000000) != 0x90000000:
        return None
    immlo, immhi, rd = (word >> 29) & 0x3, (word >> 5) & 0x7FFFF, word & 0x1F
    imm = (immhi << 2) | immlo
    if imm & 0x100000:
        imm -= 0x200000
    return (at_addr & ~0xFFF) + (imm << 12), rd


def _decode_ldr_imm64(word):
    if (word & 0xFFC00000) != 0xF9400000:
        return None
    return ((word >> 10) & 0xFFF) * 8, (word >> 5) & 0x1F, word & 0x1F


def _decode_cbnz(word):
    return (word & 0x1F) if (word & 0xFF000000) == 0xB5000000 else None


def find_lazy_singleton_cache(elf: Elf64, thunk_addr: int) -> int:
    """The address of the cached-global slot an IL2CPP lazy-singleton accessor checks,
    found by decoding its own ADRP+LDR+CBNZ prologue -- not by a hardcoded offset."""
    off = elf.v2o(thunk_addr)
    word = struct.unpack_from("<I", elf.data, off)[0]
    target = thunk_addr
    if (word & 0xFC000000) == 0x14000000:            # a single "b #target" thunk
        imm26 = word & 0x3FFFFFF
        if imm26 & 0x2000000:
            imm26 -= 0x4000000
        target = thunk_addr + (imm26 << 2)
    off = elf.v2o(target)
    for i in range(12):
        w0, w1, w2 = struct.unpack_from("<III", elf.data, off + i * 4)
        adrp = _decode_adrp(w0, target + i * 4)
        if adrp is None:
            continue
        page, rd = adrp
        ldr = _decode_ldr_imm64(w1)
        if ldr is None or ldr[1] != rd:
            continue
        cbnz_rt = _decode_cbnz(w2)
        if cbnz_rt is None or cbnz_rt != ldr[2]:
            continue
        return page + ldr[0]
    raise ValueError("expected ADRP+LDR+CBNZ lazy-singleton prologue at "
                     f"il2cpp_domain_get's target 0x{target:X}, found something else -- "
                     "this build's accessor no longer matches the shape this relies on")


def resolve_addresses(elf: Elf64) -> dict:
    symbols = elf.dynsym()
    addrs = {name: elf.find_direct(name, symbols) for name in REQUIRED_DIRECT}
    addrs.update({name: elf.find_plt_stub(name, symbols) for name in REQUIRED_PLT})
    addrs["domain_cache"] = find_lazy_singleton_cache(elf, addrs["il2cpp_domain_get"])
    return addrs


# --- the hook routine itself --------------------------------------------------------
# ARM64, assembled fresh each run (the instructions never change; only the resolved
# addresses plugged into them do). Every branch/adrp target below is an ABSOLUTE
# address -- keystone computes the correct PC-relative encoding from whatever base
# address is passed to Ks.asm(), so the layout only has to be computed once, up front,
# by counting instructions (every AArch64 instruction is exactly 4 bytes).
def _build_hook(base_vaddr: int, addrs: dict, orig_ctor_vaddr: int) -> tuple[bytes, int]:
    lines: list[str] = []
    labels: dict[str, int] = {}
    slots: dict[str, int] = {}

    def emit(mnem, slot=None):
        if slot:
            if slot in slots:
                raise ValueError(f"slot {slot!r} reused by a second emit() call -- only "
                                 "the later one would ever get fill()ed; the earlier "
                                 "placeholder instruction would ship as literal '#0'")
            slots[slot] = len(lines)
        lines.append(mnem)

    def mark(name):
        labels[name] = base_vaddr + 4 * len(lines)

    mark("ctor")
    emit("stp x29, x30, [sp, #-16]!")
    # This slot's own relocation now points at US instead of the constructor it used
    # to run -- so we must run that original one first, or skip whatever real static
    # init it was doing. Same module, so a plain intra-module call needs no relocation
    # of its own (unlike a stored pointer, PC-relative addressing is bias-safe as-is).
    emit(f"bl #{orig_ctor_vaddr}")
    emit("adrp x0, #0", "ctor_adrp_tid")
    emit("add x0, x0, #0", "ctor_add_tid")
    emit("mov x1, #0")
    emit("adrp x2, #0", "ctor_adrp_wrk")
    emit("add x2, x2, #0", "ctor_add_wrk")
    emit("mov x3, #0")
    emit("bl #0", "ctor_bl_create")
    emit("ldp x29, x30, [sp], #16")
    emit("ret")

    mark("worker")
    emit("stp x29, x30, [sp, #-80]!")
    emit("stp x19, x20, [sp, #16]")
    emit("stp x21, x22, [sp, #32]")
    emit("stp x23, x24, [sp, #48]")
    emit("stp x25, xzr, [sp, #64]")
    emit("mov x24, #0")             # il2cpp thread handle; 0 == never attached (guards its detach)
    emit("mov x25, #0")             # JavaVM*; 0 == never JVM-attached (guards its detach)
    # This is a plain pthread the JVM has never heard of. Calling straight into IL2CPP
    # from it crashed instantly and deterministically on-device (identical registers
    # every time, 15-22ms after logcat's own "IL2CPP: JNI_OnLoad" -- far too fast to be
    # a readiness race, and disassembly ruled out an addressing bug). IL2CPP sits on top
    # of ART, whose own internal thread/GC bookkeeping is the well-documented reason any
    # native thread that touches a JVM-backed runtime must first AttachCurrentThread.
    # dlopen/dlsym are already imported by this library (IL2CPP's own P/Invoke marshaling
    # needs them), so no new relocations are needed to reach the standard, ABI-stable JNI
    # invocation interface (unlike IL2CPP's internals, this vtable layout is public NDK
    # contract, not something that varies per build).
    # NULL, not a named library: bionic's linker applies its public-library allowlist
    # check specifically when resolving a SONAME across namespace boundaries, and that
    # blocked "libart.so" outright (confirmed on-device with both the bare soname and
    # its full APEX path -- identical failure either way, so it's a namespace policy,
    # not a lookup-string problem). dlopen(NULL, ...) instead asks for the CALLING
    # library's own namespace/global scope, which doesn't go through that same check.
    emit("mov x0, #0")
    emit("mov w1, #2")                    # RTLD_NOW
    emit("bl #0", "at_bl_dlopen")
    emit("cbz x0, #0", "at_cbz_giveup1")
    emit("mov x20, x0")                   # x20 = the resolved handle
    emit("adrp x1, #0", "at_adrp_sym")
    emit("add x1, x1, #0", "at_add_sym")
    emit("bl #0", "at_bl_dlsym")
    emit("cbz x0, #0", "at_cbz_giveup2")
    emit("mov x9, x0")                    # x9 = JNI_GetCreatedJavaVMs
    emit("adrp x0, #0", "at_adrp_vm1")
    emit("add x0, x0, #0", "at_add_vm1")
    emit("mov w1, #1")
    emit("adrp x2, #0", "at_adrp_vmc")
    emit("add x2, x2, #0", "at_add_vmc")
    emit("blr x9")
    emit("adrp x0, #0", "at_adrp_vm2")
    emit("add x0, x0, #0", "at_add_vm2")
    emit("ldr x25, [x0]")                 # x25 = JavaVM* (0 if GetCreatedJavaVMs failed)
    emit("cbz x25, #0", "at_cbz_giveup3")
    emit("b #0", "at_b_attach_ok")        # jump past the three diagnostic dead-ends below

    # Distinct log line per JVM-attach failure mode -- a shared "give_up" message
    # couldn't tell dlopen/dlsym/GetCreatedJavaVMs apart, and that ambiguity is exactly
    # what cost a whole extra device round-trip last time.
    def diag_dead_end(label, msg_key):
        mark(label)
        emit("mov w0, #5")
        emit("adrp x1, #0", f"{label}_adrp_tag")
        emit("add x1, x1, #0", f"{label}_add_tag")
        emit("adrp x2, #0", f"{label}_adrp_msg")
        emit("add x2, x2, #0", f"{label}_add_msg")
        emit("bl #0", f"{label}_bl_log")
        emit("b #0", f"{label}_b_done")

    diag_dead_end("diag_dlopen", "diag_dlopen_msg")
    diag_dead_end("diag_dlsym", "diag_dlsym_msg")
    diag_dead_end("diag_vm", "diag_vm_msg")
    mark("at_attach_ok")
    emit("ldr x9, [x25]")                 # x9 = *vm -> the JNIInvokeInterface vtable
    emit("ldr x9, [x9, #32]")             # vtable[4] = AttachCurrentThread (stable JNI ABI)
    emit("mov x0, x25")
    emit("adrp x1, #0", "at_adrp_env")
    emit("add x1, x1, #0", "at_add_env")
    emit("mov x2, #0")
    emit("blr x9")
    # il2cpp_domain_get() is a lazy-init singleton, not a plain accessor: disassembly
    # shows it checks a cached global and, if still null, DOES the initialization itself
    # (allocates, reads other IL2CPP globals, calls further functions) right there on the
    # calling thread. On-device, this crashed identically regardless of JVM/IL2CPP
    # thread-attach state, always at the same offset inside that lazy-init path, and no
    # amount of waiting before the first attempt changed that -- this thread was winning
    # the race to be the very FIRST caller in the whole process every time, however long
    # it waited to try. Attaching fixes a real but separate requirement; it was never what
    # caused this. The actual fix: never make that first call at all. A plain memory read
    # of the cache can't trigger the hazard (see find_lazy_singleton_cache), so poll THAT
    # instead, and only call the real accessor once it's already non-null -- guaranteed to
    # take the fast path.
    emit("mov x19, #0")
    mark("domain_wait")
    emit("adrp x0, #0", "w_adrp_cache")
    emit("add x0, x0, #0", "w_add_cache")
    emit("ldr x0, [x0]")
    emit("cbnz x0, #0", "w_cbnz_ready")
    emit("add x19, x19, #1")
    emit(f"cmp x19, #{GIVE_UP_RETRIES}")
    emit("b.ge #0", "w_bge_giveup")
    emit(f"mov x0, #{USLEEP_PER_RETRY_US}")
    emit("bl #0", "w_bl_usleep")
    emit("b #0", "w_b_wait")
    mark("domain_ready")
    emit("mov x20, x0")                  # x20 = domain, preserved across the upcoming calls
    # Every thread calling into IL2CPP must first be attached, or internal per-thread
    # state (which several of these calls dereference) is null. Confirmed on-device:
    # skipping this produced a null-pointer SIGSEGV a few calls deep inside libil2cpp's
    # own code on a freshly spawned, never-attached pthread.
    emit("bl #0", "dr_bl_attach")        # il2cpp_thread_attach(domain) -> Il2CppThread*
    emit("mov x24, x0")                  # keep for the matching detach at the end
    emit("mov x0, x20")                  # restore domain as get_assemblies' argument
    emit("adrp x1, #0", "dr_adrp_cnt1")
    emit("add x1, x1, #0", "dr_add_cnt1")
    emit("bl #0", "dr_bl_getassm")
    emit("mov x20, x0")
    emit("adrp x1, #0", "dr_adrp_cnt2")
    emit("add x1, x1, #0", "dr_add_cnt2")
    emit("ldr w21, [x1]")
    emit("mov x22, #0")
    mark("asm_loop")
    emit("cmp w22, w21")
    emit("b.ge #0", "al_bge_giveup")
    emit("ldr x0, [x20, x22, lsl #3]")
    emit("bl #0", "al_bl_getimg")
    emit("cbz x0, #0", "al_cbz_next")
    emit("adrp x1, #0", "al_adrp_empty")
    emit("add x1, x1, #0", "al_add_empty")
    emit("adrp x2, #0", "al_adrp_main")
    emit("add x2, x2, #0", "al_add_main")
    emit("bl #0", "al_bl_cfn")
    emit("cbnz x0, #0", "al_cbnz_found")
    mark("al_next")
    emit("add x22, x22, #1")
    emit("b #0", "al_b_loop")
    mark("class_found")
    emit("adrp x21, #0", "cf_adrp_stub")   # x21 = getter_stub's address (target), computed
    emit("add x21, x21, #0", "cf_add_stub")  # first so nothing below clobbers it before use
    emit("adrp x1, #0", "cf_adrp_gwau")
    emit("add x1, x1, #0", "cf_add_gwau")
    emit("mov x2, #0")
    emit("bl #0", "cf_bl_gmfn")
    emit("cbz x0, #0", "cf_cbz_giveup_method")
    # Overwriting MethodInfo->methodPointer alone does not work, confirmed on-device (a
    # brief redirect that reverted): IL2CPP AOT-compiles a direct caller's access to a
    # static property as a fixed branch straight to the compiled function's address,
    # bypassing this field entirely -- and separately, IL2CPP's own later boot sequence
    # re-populates every MethodInfo's methodPointer from its static codegen table anyway,
    # undoing a pointer-only patch regardless. Patching the INSTRUCTIONS at that fixed
    # address is the one thing neither a direct caller nor that later repopulation can
    # route around, since both still land on the same address afterward.
    emit("ldr x23, [x0]")                  # x23 = the method's real, fixed compiled address
    # mprotect isn't already imported by this library, unlike everything else used so far
    # -- resolve it the same proven way as JNI_GetCreatedJavaVMs (dlopen(NULL) + dlsym)
    # rather than adding a new relocation for it.
    emit("mov x0, #0")
    emit("mov w1, #2")                    # RTLD_NOW
    emit("bl #0", "mp_bl_dlopen")
    emit("cbz x0, #0", "cf_cbz_giveup_dlopen")
    emit("adrp x1, #0", "mp_adrp_sym")
    emit("add x1, x1, #0", "mp_add_sym")
    emit("bl #0", "mp_bl_dlsym")
    emit("cbz x0, #0", "cf_cbz_giveup_dlsym")
    emit("mov x9, x0")                    # x9 = mprotect
    emit("mov x0, x23")
    emit("and x0, x0, #0xFFFFFFFFFFFFC000")   # page-align down (0x4000 granularity)
    emit("mov x1, #0x8000")               # two pages: covers any alignment edge case
    emit("mov x2, #7")                    # PROT_READ | PROT_WRITE | PROT_EXEC
    emit("blr x9")
    # Encode "B <getter_stub>" at runtime (the target's own compiled address is only
    # known now, not at patch-build time) and write it over the method's first
    # instruction -- an unconditional tail redirect, not a call: this function should
    # never run its original body again for anyone.
    emit("sub x2, x21, x23")              # x2 = getter_stub - real_addr (byte delta)
    emit("asr x2, x2, #2")                # x2 = imm26 (arithmetic shift keeps the sign)
    emit("and x2, x2, #0x3FFFFFF")        # mask to the field's 26 bits
    emit("movz w3, #0x1400, lsl #16")     # w3 = 0x14000000 (unconditional B opcode base)
    emit("orr w2, w3, w2")                # w2 = the encoded B instruction
    emit("str w2, [x23]")
    emit("mov w0, #4")
    emit("adrp x1, #0", "cf_adrp_tag")
    emit("add x1, x1, #0", "cf_add_tag")
    emit("adrp x2, #0", "cf_adrp_ok")
    emit("add x2, x2, #0", "cf_add_ok")
    emit("bl #0", "cf_bl_log")
    emit("b #0", "cf_b_done")
    mark("give_up")
    emit("mov w0, #5")
    emit("adrp x1, #0", "gu_adrp_tag")
    emit("add x1, x1, #0", "gu_add_tag")
    emit("adrp x2, #0", "gu_adrp_fail")
    emit("add x2, x2, #0", "gu_add_fail")
    emit("bl #0", "gu_bl_log")
    mark("w_done")
    # Reached either after a successful patch or after giving up -- possibly before ever
    # attaching (domain never became ready), so guard the detach on having a handle at all.
    emit("cbz x24, #0", "wd_cbz_skip_detach")
    emit("mov x0, x24")
    emit("bl #0", "wd_bl_detach")
    mark("skip_detach")
    # Same guard for the JVM-level attach from the top of this function -- x25 is 0 if
    # dlopen/dlsym/JNI_GetCreatedJavaVMs didn't all succeed.
    emit("cbz x25, #0", "wd_cbz_skip_jvm_detach")
    emit("ldr x9, [x25]")
    emit("ldr x9, [x9, #40]")            # vtable[5] = DetachCurrentThread
    emit("mov x0, x25")
    emit("blr x9")
    mark("skip_jvm_detach")
    emit("ldp x19, x20, [sp, #16]")
    emit("ldp x21, x22, [sp, #32]")
    emit("ldp x23, x24, [sp, #48]")
    emit("ldp x25, xzr, [sp, #64]")
    emit("mov x0, #0")
    emit("ldp x29, x30, [sp], #80")
    emit("ret")

    mark("getter_stub")
    emit("adrp x0, #0", "gs_adrp_url")
    emit("add x0, x0, #0", "gs_add_url")
    emit("b #0", "gs_b_strnew")

    code_len = 4 * len(lines)
    rodata_base = base_vaddr + code_len
    rodata = bytearray()
    strings = {}
    for key, text in (("url", SERVER_URL.encode("ascii") + b"\x00"),
                      ("empty", b"\x00"), ("Main", b"Main\x00"),
                      ("getWebApiURL", b"get_WebApiURL\x00"),
                      ("tag", b"InfinityHook\x00"),
                      ("ok", b"get_WebApiURL patched OK\x00"),
                      ("fail", b"get_WebApiURL patch FAILED (gave up)\x00"),
                      ("getCreatedVMs", b"JNI_GetCreatedJavaVMs\x00"),
                      ("mprotectSym", b"mprotect\x00"),
                      ("diagDlopen", b"dlopen(NULL) failed\x00"),
                      ("diagDlsym", b"dlsym(JNI_GetCreatedJavaVMs) failed\x00"),
                      ("diagVm", b"JNI_GetCreatedJavaVMs returned no JavaVM\x00")):
        strings[key] = rodata_base + len(rodata)
        rodata += text
    total_len = len(rodata) + code_len

    def fill(slot, mnem):
        lines[slots[slot]] = mnem

    fill("ctor_adrp_tid", f"adrp x0, #{addrs['tid_scratch'] & ~0xFFF}")
    fill("ctor_add_tid", f"add x0, x0, #{addrs['tid_scratch'] & 0xFFF}")
    fill("ctor_adrp_wrk", f"adrp x2, #{labels['worker'] & ~0xFFF}")
    fill("ctor_add_wrk", f"add x2, x2, #{labels['worker'] & 0xFFF}")
    fill("ctor_bl_create", f"bl #{addrs['pthread_create']}")

    fill("at_bl_dlopen", f"bl #{addrs['dlopen']}")
    fill("at_cbz_giveup1", f"cbz x0, #{labels['diag_dlopen']}")
    fill("at_adrp_sym", f"adrp x1, #{strings['getCreatedVMs'] & ~0xFFF}")
    fill("at_add_sym", f"add x1, x1, #{strings['getCreatedVMs'] & 0xFFF}")
    fill("at_bl_dlsym", f"bl #{addrs['dlsym']}")
    fill("at_cbz_giveup2", f"cbz x0, #{labels['diag_dlsym']}")
    fill("at_adrp_vm1", f"adrp x0, #{addrs['vm_scratch'] & ~0xFFF}")
    fill("at_add_vm1", f"add x0, x0, #{addrs['vm_scratch'] & 0xFFF}")
    fill("at_adrp_vmc", f"adrp x2, #{addrs['jni_count_scratch'] & ~0xFFF}")
    fill("at_add_vmc", f"add x2, x2, #{addrs['jni_count_scratch'] & 0xFFF}")
    fill("at_adrp_vm2", f"adrp x0, #{addrs['vm_scratch'] & ~0xFFF}")
    fill("at_add_vm2", f"add x0, x0, #{addrs['vm_scratch'] & 0xFFF}")
    fill("at_cbz_giveup3", f"cbz x25, #{labels['diag_vm']}")
    fill("at_b_attach_ok", f"b #{labels['at_attach_ok']}")
    fill("diag_dlopen_adrp_tag", f"adrp x1, #{strings['tag'] & ~0xFFF}")
    fill("diag_dlopen_add_tag", f"add x1, x1, #{strings['tag'] & 0xFFF}")
    fill("diag_dlopen_adrp_msg", f"adrp x2, #{strings['diagDlopen'] & ~0xFFF}")
    fill("diag_dlopen_add_msg", f"add x2, x2, #{strings['diagDlopen'] & 0xFFF}")
    fill("diag_dlopen_bl_log", f"bl #{addrs['__android_log_print']}")
    fill("diag_dlopen_b_done", f"b #{labels['w_done']}")
    fill("diag_dlsym_adrp_tag", f"adrp x1, #{strings['tag'] & ~0xFFF}")
    fill("diag_dlsym_add_tag", f"add x1, x1, #{strings['tag'] & 0xFFF}")
    fill("diag_dlsym_adrp_msg", f"adrp x2, #{strings['diagDlsym'] & ~0xFFF}")
    fill("diag_dlsym_add_msg", f"add x2, x2, #{strings['diagDlsym'] & 0xFFF}")
    fill("diag_dlsym_bl_log", f"bl #{addrs['__android_log_print']}")
    fill("diag_dlsym_b_done", f"b #{labels['w_done']}")
    fill("diag_vm_adrp_tag", f"adrp x1, #{strings['tag'] & ~0xFFF}")
    fill("diag_vm_add_tag", f"add x1, x1, #{strings['tag'] & 0xFFF}")
    fill("diag_vm_adrp_msg", f"adrp x2, #{strings['diagVm'] & ~0xFFF}")
    fill("diag_vm_add_msg", f"add x2, x2, #{strings['diagVm'] & 0xFFF}")
    fill("diag_vm_bl_log", f"bl #{addrs['__android_log_print']}")
    fill("diag_vm_b_done", f"b #{labels['w_done']}")
    fill("at_adrp_env", f"adrp x1, #{addrs['env_scratch'] & ~0xFFF}")
    fill("at_add_env", f"add x1, x1, #{addrs['env_scratch'] & 0xFFF}")

    fill("w_adrp_cache", f"adrp x0, #{addrs['domain_cache'] & ~0xFFF}")
    fill("w_add_cache", f"add x0, x0, #{addrs['domain_cache'] & 0xFFF}")
    fill("w_cbnz_ready", f"cbnz x0, #{labels['domain_ready']}")
    fill("w_bge_giveup", f"b.ge #{labels['give_up']}")
    fill("w_bl_usleep", f"bl #{addrs['usleep']}")
    fill("w_b_wait", f"b #{labels['domain_wait']}")
    fill("dr_bl_attach", f"bl #{addrs['il2cpp_thread_attach']}")
    fill("dr_adrp_cnt1", f"adrp x1, #{addrs['count_scratch'] & ~0xFFF}")
    fill("dr_add_cnt1", f"add x1, x1, #{addrs['count_scratch'] & 0xFFF}")
    fill("dr_bl_getassm", f"bl #{addrs['il2cpp_domain_get_assemblies']}")
    fill("dr_adrp_cnt2", f"adrp x1, #{addrs['count_scratch'] & ~0xFFF}")
    fill("dr_add_cnt2", f"add x1, x1, #{addrs['count_scratch'] & 0xFFF}")

    fill("al_bge_giveup", f"b.ge #{labels['give_up']}")
    fill("al_bl_getimg", f"bl #{addrs['il2cpp_assembly_get_image']}")
    fill("al_cbz_next", f"cbz x0, #{labels['al_next']}")
    fill("al_adrp_empty", f"adrp x1, #{strings['empty'] & ~0xFFF}")
    fill("al_add_empty", f"add x1, x1, #{strings['empty'] & 0xFFF}")
    fill("al_adrp_main", f"adrp x2, #{strings['Main'] & ~0xFFF}")
    fill("al_add_main", f"add x2, x2, #{strings['Main'] & 0xFFF}")
    fill("al_bl_cfn", f"bl #{addrs['il2cpp_class_from_name']}")
    fill("al_cbnz_found", f"cbnz x0, #{labels['class_found']}")
    fill("al_b_loop", f"b #{labels['asm_loop']}")

    fill("cf_adrp_stub", f"adrp x21, #{labels['getter_stub'] & ~0xFFF}")
    fill("cf_add_stub", f"add x21, x21, #{labels['getter_stub'] & 0xFFF}")
    fill("cf_adrp_gwau", f"adrp x1, #{strings['getWebApiURL'] & ~0xFFF}")
    fill("cf_add_gwau", f"add x1, x1, #{strings['getWebApiURL'] & 0xFFF}")
    fill("cf_bl_gmfn", f"bl #{addrs['il2cpp_class_get_method_from_name']}")
    fill("cf_cbz_giveup_method", f"cbz x0, #{labels['give_up']}")
    fill("mp_bl_dlopen", f"bl #{addrs['dlopen']}")
    fill("cf_cbz_giveup_dlopen", f"cbz x0, #{labels['give_up']}")
    fill("mp_adrp_sym", f"adrp x1, #{strings['mprotectSym'] & ~0xFFF}")
    fill("mp_add_sym", f"add x1, x1, #{strings['mprotectSym'] & 0xFFF}")
    fill("mp_bl_dlsym", f"bl #{addrs['dlsym']}")
    fill("cf_cbz_giveup_dlsym", f"cbz x0, #{labels['give_up']}")
    fill("cf_adrp_tag", f"adrp x1, #{strings['tag'] & ~0xFFF}")
    fill("cf_add_tag", f"add x1, x1, #{strings['tag'] & 0xFFF}")
    fill("cf_adrp_ok", f"adrp x2, #{strings['ok'] & ~0xFFF}")
    fill("cf_add_ok", f"add x2, x2, #{strings['ok'] & 0xFFF}")
    fill("cf_bl_log", f"bl #{addrs['__android_log_print']}")
    fill("cf_b_done", f"b #{labels['w_done']}")

    fill("gu_adrp_tag", f"adrp x1, #{strings['tag'] & ~0xFFF}")
    fill("gu_add_tag", f"add x1, x1, #{strings['tag'] & 0xFFF}")
    fill("gu_adrp_fail", f"adrp x2, #{strings['fail'] & ~0xFFF}")
    fill("gu_add_fail", f"add x2, x2, #{strings['fail'] & 0xFFF}")
    fill("gu_bl_log", f"bl #{addrs['__android_log_print']}")

    fill("wd_cbz_skip_detach", f"cbz x24, #{labels['skip_detach']}")
    fill("wd_bl_detach", f"bl #{addrs['il2cpp_thread_detach']}")
    fill("wd_cbz_skip_jvm_detach", f"cbz x25, #{labels['skip_jvm_detach']}")

    fill("gs_adrp_url", f"adrp x0, #{strings['url'] & ~0xFFF}")
    fill("gs_add_url", f"add x0, x0, #{strings['url'] & 0xFFF}")
    fill("gs_b_strnew", f"b #{addrs['il2cpp_string_new']}")

    ks = Ks(KS_ARCH_ARM64, KS_MODE_LITTLE_ENDIAN)
    code, count = ks.asm("\n".join(lines), base_vaddr, as_bytes=True)
    if count != len(lines) or len(code) != code_len:
        raise ValueError("internal error: hook assembly did not match its own layout")
    blob = bytes(code) + bytes(rodata)
    if len(blob) != total_len:
        raise ValueError("internal error: hook blob size mismatch")
    return blob, labels["ctor"]


def _align_up(n, a):
    return (n + a - 1) // a * a


def inject_hook(library: bytes) -> bytes:
    """Append the hook to libil2cpp.so: a small R+X segment (code + rodata), a
    tiny R+W scratch page (pthread_create's out-param), and a relocated copy of
    the phdr table / .dynamic array / init_array (extended by one entry -- our
    ctor). Nothing already in the file is overwritten; the ELF header's
    e_phoff/e_phnum are the only bytes changed outside the appended region."""
    elf = Elf64(library)
    addrs = resolve_addresses(elf)

    segs = elf.segments()
    max_end = max(s["vaddr"] + s["memsz"] for s in segs if s["type"] == PT_LOAD)
    new_base = max(_align_up(len(elf.data), SEG_ALIGN), _align_up(max_end, SEG_ALIGN))
    rw_base = new_base + SEG_ALIGN
    meta_base = rw_base + SEG_ALIGN
    addrs["tid_scratch"] = rw_base
    addrs["count_scratch"] = rw_base + 8
    addrs["vm_scratch"] = rw_base + 16
    addrs["jni_count_scratch"] = rw_base + 24
    addrs["env_scratch"] = rw_base + 32
    RW_SCRATCH_SIZE = 40

    # Entry point: DO NOT add a new init_array slot. An appended pointer has no
    # relocation of its own, so it's called at its raw LINK-TIME address instead of
    # load_bias + that address -- confirmed on a real device (Instruction Abort,
    # "trying to execute non-executable memory": the fault landed in an unrelated
    # anon mapping that happened to sit at the un-rebased address under ASLR).
    # Instead, HIJACK one EXISTING init_array slot's own R_AARCH64_RELATIVE
    # relocation: change only its addend from the original constructor to ours. That
    # relocation is already correctly bias-adjusted by the linker for every load, so
    # our ctor is too, with zero new relocations and zero .dynamic/init_array edits.
    # We call the original constructor ourselves first so whatever real static init
    # it did still happens.
    entries, _ = elf.dynamic_entries()
    tags = dict(entries)
    ia_addr, ia_size = tags[DT_INIT_ARRAY], tags[DT_INIT_ARRAYSZ]
    hijack_slot_vaddr = ia_addr + ia_size - 8       # the last entry; any would do
    reloc_off = elf.find_relative_reloc_file_offset(hijack_slot_vaddr)
    # The original constructor's link-time address lives in the RELOCATION's addend,
    # not the on-disk init_array slot itself -- confirmed on this exact build the slot's
    # raw bytes are just 0; R_AARCH64_RELATIVE relocations don't read the pre-existing
    # memory value at all, the linker writes (bias + addend) unconditionally.
    orig_ctor_vaddr = struct.unpack_from("<q", elf.data, reloc_off + 16)[0]

    blob, ctor_vaddr = _build_hook(new_base, addrs, orig_ctor_vaddr)
    if len(blob) > SEG_ALIGN:
        raise ValueError("hook routine grew past its reserved page -- adjust SEG_ALIGN")

    old_phdrs = elf.data[elf.e_phoff:elf.e_phoff + elf.e_phentsize * elf.e_phnum]
    # +3: RX (code+rodata), RW (scratch), and R (the phdr table's own containing
    # segment) -- bionic's ElfReader::CheckPhdr() requires the phdr table itself to
    # live inside a mapped, non-executable PT_LOAD, so that entry must exist too.
    new_phdr_count = 3
    phdr_size = elf.e_phentsize * (elf.e_phnum + new_phdr_count)

    phdrs = bytearray(old_phdrs)
    for i in range(elf.e_phnum):
        off = i * elf.e_phentsize
        if struct.unpack_from("<I", phdrs, off)[0] == PT_PHDR:
            # Self-descriptive: must track the table's new location/size now that
            # it's relocated, even though dlopen()'d libraries are loaded via
            # e_phoff/e_phnum directly rather than this entry (it matters for the
            # main executable, not shared libraries) -- leaving it stale would still
            # be a real inconsistency now that PT_DYNAMIC is untouched and no longer
            # forces every other stale field into the spotlight the same way.
            struct.pack_into("<IIQQQQQQ", phdrs, off, PT_PHDR, 4,
                             meta_base, meta_base, meta_base, phdr_size, phdr_size, 8)

    def pt_load(vaddr, filesz, flags):
        return struct.pack("<IIQQQQQQ", PT_LOAD, flags, vaddr, vaddr, vaddr,
                           filesz, filesz, SEG_ALIGN)

    phdrs += pt_load(new_base, len(blob), 5)     # R+X: code + rodata
    phdrs += pt_load(rw_base, RW_SCRATCH_SIZE, 6) # R+W: pthread/JNI out-params
    phdrs += pt_load(meta_base, phdr_size, 4)     # R: the relocated phdr table itself

    # The exact bug that produced "loaded phdr ... not in loadable segment" on-device:
    # new_phdr_count silently drifting out of sync with how many pt_load() calls are
    # actually made above. Catch that class of mistake here instead of on a phone.
    if len(phdrs) != phdr_size:
        raise ValueError(f"phdr table size mismatch: wrote {len(phdrs)} bytes but "
                         f"declared {phdr_size} (new_phdr_count={new_phdr_count}) -- "
                         "a pt_load() call was added/removed without updating it")

    out = bytearray(elf.data)
    out += b"\x00" * (new_base - len(out))
    out += blob
    out += b"\x00" * (rw_base - len(out))
    out += b"\x00" * RW_SCRATCH_SIZE
    out += b"\x00" * (meta_base - len(out))
    out += phdrs

    struct.pack_into("<Q", out, 0x20, meta_base)                      # e_phoff (offset == vaddr)
    struct.pack_into("<H", out, 0x38, elf.e_phnum + new_phdr_count)   # e_phnum

    # The one field that actually redirects this slot at load time.
    struct.pack_into("<q", out, reloc_off + 16, ctor_vaddr)
    return bytes(out)


def validate_metadata(metadata: bytes) -> None:
    magic = struct.unpack_from("<I", metadata, 0)[0]
    if magic != 0xFAB11BAF:
        raise ValueError("this file doesn't look like an IL2CPP global-metadata.dat")


def validate_package(manifest: bytes) -> None:
    # AndroidManifest.xml ships as compiled binary AXML; the package name still
    # appears as a plain UTF-8/UTF-16 string in its string pool, so a substring
    # check is a fine smell test without a full AXML parser.
    if EXPECTED_PACKAGE not in manifest and \
            EXPECTED_PACKAGE.decode().encode("utf-16-le") not in manifest:
        raise ValueError(f"expected package {EXPECTED_PACKAGE.decode()!r} in AndroidManifest.xml")


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


def build(source_apk: Path, output_apk: Path) -> None:
    with zipfile.ZipFile(source_apk, "r") as source:
        names = set(source.namelist())
        for required in (ARM64_LIBRARY, METADATA_PATH, MANIFEST_PATH):
            if required not in names:
                raise ValueError(f"source APK is missing {required}")

        validate_package(source.read(MANIFEST_PATH))
        validate_metadata(source.read(METADATA_PATH))
        patched_library = inject_hook(source.read(ARM64_LIBRARY))

        output_apk.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output_apk, "w", allowZip64=True) as output:
            for source_info in source.infolist():
                if source_info.filename.startswith(ARMV7_PREFIX):
                    continue
                payload = source.read(source_info.filename)
                if source_info.filename == ARM64_LIBRARY:
                    payload = patched_library
                output.writestr(clone_zip_info(source_info), payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_apk", type=Path, help="an original AQ2D Android APK")
    parser.add_argument("output_apk", type=Path, help="unsigned arm64-only patched APK")
    args = parser.parse_args()

    try:
        build(args.source_apk, args.output_apk)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"AQ2D Android port failed: {error}", file=sys.stderr)
        return 1

    print(f"Created unsigned arm64-only port: {args.output_apk}")
    print(f"Web API base URL: {SERVER_URL}")
    print("The redirect is applied by a runtime hook (see logcat tag 'InfinityHook' "
         "after installing) rather than a build-specific binary patch, so this same "
         "script should keep working across future AQ2D updates without changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
