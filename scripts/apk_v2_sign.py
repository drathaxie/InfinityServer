"""Pure-Python APK Signature Scheme v2 signer (no apksigner/JRE dependency).

Implements https://source.android.com/docs/security/features/apksigning/v2
directly: RSA-2048 + PKCS#1 v1.5 + SHA-256 only (one signer, one algorithm --
enough for a locally-sideloaded test build; no v3 key-rotation lineage).

An APK, once signed, is four regions in this order: (1) the zip entries'
contents, (2) the APK Signing Block (inserted here, between the entries and
the central directory -- ordinary zip readers just see it as unused space
before the central directory and ignore it), (3) the zip central directory,
(4) the End Of Central Directory record. The signature covers a running
SHA-256 "content digest" computed over (1), (3), and (4) -- NOT a digest of
the raw bytes directly, but Android's specific chunked scheme (see
`_content_digest`), because that's what lets a verifier check integrity
without buffering the whole (potentially huge) APK in memory at once.
"""

from __future__ import annotations

import hashlib
import struct
import zipfile
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

SIGNATURE_RSA_PKCS1_V1_5_WITH_SHA256 = 0x0103
APK_SIG_BLOCK_MAGIC = b"APK Sig Block 42"
APK_SIGNATURE_SCHEME_V2_ID = 0x7109871A
CHUNK_SIZE = 1024 * 1024

EOCD_SIG = b"PK\x05\x06"


# --- identity: a persistent, local, self-signed RSA-2048 signer --------------------------
def load_or_create_identity(base_path: Path):
    """A local test-signing identity, generated once and reused afterward (Android
    treats a certificate change as a different app and refuses to install it as an
    update over a previous local build)."""
    key_path = base_path.with_suffix(".key.pem")
    cert_path = base_path.with_suffix(".cert.pem")
    if key_path.exists() and cert_path.exists():
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        return key, cert

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    import datetime
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "InfinityServer Local")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
           .subject_name(name).issuer_name(name)
           .public_key(key.public_key())
           .serial_number(x509.random_serial_number())
           .not_valid_before(now - datetime.timedelta(days=1))
           .not_valid_after(now + datetime.timedelta(days=10000))
           .sign(key, hashes.SHA256()))

    base_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key, cert


# --- the v2-specific chunked content digest -----------------------------------------------
def _chunk_digests(data: bytes) -> list[bytes]:
    out = []
    for i in range(0, len(data), CHUNK_SIZE):
        chunk = data[i:i + CHUNK_SIZE]
        out.append(hashlib.sha256(b"\xa5" + struct.pack("<I", len(chunk)) + chunk).digest())
    return out


def _content_digest(regions: list[bytes]) -> bytes:
    all_chunk_digests: list[bytes] = []
    for region in regions:
        all_chunk_digests.extend(_chunk_digests(region))
    top = b"\x5a" + struct.pack("<I", len(all_chunk_digests)) + b"".join(all_chunk_digests)
    return hashlib.sha256(top).digest()


# --- length-prefixed TLV helpers matching the v2 block's nested framing ------------------
def _u32(n: int) -> bytes:
    return struct.pack("<I", n)


def _lp(data: bytes) -> bytes:
    """One length-prefixed field: a uint32 byte-count followed by the bytes."""
    return _u32(len(data)) + data


def _build_v2_signed_data(content_digest: bytes, cert_der: bytes) -> bytes:
    digest_record = _lp(_u32(SIGNATURE_RSA_PKCS1_V1_5_WITH_SHA256) + _lp(content_digest))
    digests_seq = _lp(digest_record)
    cert_record = _lp(cert_der)
    certs_seq = _lp(cert_record)
    additional_attrs_seq = _lp(b"")
    return digests_seq + certs_seq + additional_attrs_seq


def _build_v2_block_value(signed_data: bytes, signature: bytes, public_key_der: bytes) -> bytes:
    signature_record = _lp(_u32(SIGNATURE_RSA_PKCS1_V1_5_WITH_SHA256) + _lp(signature))
    signatures_seq = _lp(signature_record)
    signer = _lp(signed_data) + signatures_seq + _lp(public_key_der)
    signers_seq = _lp(_lp(signer))
    return signers_seq


def _wrap_signing_block(id_value_pairs: list[tuple[int, bytes]]) -> bytes:
    pairs_bytes = b"".join(
        struct.pack("<Q", 4 + len(value)) + _u32(pair_id) + value
        for pair_id, value in id_value_pairs)
    size_value = len(pairs_bytes) + 8 + 16
    return (struct.pack("<Q", size_value) + pairs_bytes +
           struct.pack("<Q", size_value) + APK_SIG_BLOCK_MAGIC)


def _find_eocd_offset(data: bytes) -> int:
    tail_start = max(0, len(data) - 22 - 65535)
    idx = data.rfind(EOCD_SIG, tail_start)
    if idx < 0:
        raise ValueError("not a valid zip: no End Of Central Directory record found")
    return idx


def sign_v2(apk_bytes: bytes, key, cert) -> bytes:
    """Insert an APK Signature Scheme v2 block into an ALREADY-ALIGNED, unsigned APK.
    (Sign after aligning, same as `apksigner`/`zipalign` -- signing after alignment
    would change offsets the signature covers, invalidating them.)"""
    eocd_off = _find_eocd_offset(apk_bytes)
    (_sig, _disk, _cd_disk, _n_disk, n_total, cd_size, cd_offset,
     comment_len) = struct.unpack_from("<4sHHHHIIH", apk_bytes, eocd_off)
    comment = apk_bytes[eocd_off + 22:eocd_off + 22 + comment_len]

    contents = apk_bytes[:cd_offset]
    central_dir = apk_bytes[cd_offset:cd_offset + cd_size]

    cert_der = cert.public_bytes(serialization.Encoding.DER)
    public_key_der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)

    # Build a throwaway v2 block first purely to learn its length: the EOCD's patched
    # cd_offset (which the digest itself must cover) depends on where the real central
    # directory ends up, which depends on the block's size -- but every field's size
    # here is already fixed once the key/cert are chosen (an RSA-2048 PKCS#1v1.5
    # signature is always exactly 256 bytes), so one dry run resolves it exactly.
    dummy_signature = b"\x00" * 256
    dummy_signed_data = _build_v2_signed_data(b"\x00" * 32, cert_der)
    dummy_value = _build_v2_block_value(dummy_signed_data, dummy_signature, public_key_der)
    dummy_block = _wrap_signing_block([(APK_SIGNATURE_SCHEME_V2_ID, dummy_value)])
    block_len = len(dummy_block)

    signing_block_start = len(contents)
    real_cd_offset = signing_block_start + block_len

    # Two DIFFERENT EOCD copies, per spec: the one actually written to disk must point
    # at the real central directory (so ordinary zip readers -- and Android's own
    # installer -- still find it correctly past the signing block). The one fed into
    # the digest is a separate, in-memory-only copy with that field overwritten to the
    # SIGNING BLOCK's own start offset instead. Getting this backwards (as an earlier
    # attempt did) parses fine but produces a digest that silently doesn't match what a
    # verifier recomputes -- apksigner reported "CHUNKED_SHA256 digest mismatch" with no
    # further hint of which field was wrong.
    real_eocd = struct.pack("<4sHHHHIIH", EOCD_SIG, 0, 0, n_total, n_total,
                            cd_size, real_cd_offset, comment_len) + comment
    digest_eocd = struct.pack("<4sHHHHIIH", EOCD_SIG, 0, 0, n_total, n_total,
                              cd_size, signing_block_start, comment_len) + comment

    digest = _content_digest([contents, central_dir, digest_eocd])
    signed_data = _build_v2_signed_data(digest, cert_der)
    signature = key.sign(signed_data, padding.PKCS1v15(), hashes.SHA256())
    if len(signature) != 256:
        raise ValueError(f"unexpected RSA signature length {len(signature)} (expected 256 "
                         "for a 2048-bit key) -- the dry-run block-size assumption above "
                         "no longer holds")
    value = _build_v2_block_value(signed_data, signature, public_key_der)
    block = _wrap_signing_block([(APK_SIGNATURE_SCHEME_V2_ID, value)])
    assert len(block) == block_len, "signing block length changed between dry run and real build"

    return contents + block + central_dir + real_eocd
