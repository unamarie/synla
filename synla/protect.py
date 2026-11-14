"""Encryption at rest.

On Windows, Synla encrypts window names with DPAPI, the encryption built into Windows: only your Windows account,
on this computer, can read them. Other accounts, a copied disk or a backup see scrambled bytes.
On Mac and Linux the data is stored as is, in a folder only your account can open.

Limits, said plainly: on a computer an employer manages, a domain administrator can in principle recover DPAPI keys.
That is one reason Synla is meant for a computer you own, or use with your employer's written approval.
"""
import sys

ENCRYPTED = sys.platform == "win32"

if ENCRYPTED:
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _sig = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(_Blob)]
    _crypt32.CryptProtectData.argtypes = _sig
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p] + _sig[2:]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p

    _ENTROPY = b"synla-v1"           # ties the data to Synla, so other programs using DPAPI cannot read it by accident
    _NO_UI = 0x1                     # CRYPTPROTECT_UI_FORBIDDEN

    def _blob(data: bytes):
        buf = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    def _run(fn, data: bytes) -> bytes:
        inb, keep1 = _blob(data)
        ent, keep2 = _blob(_ENTROPY)
        out = _Blob()
        if not fn(ctypes.byref(inb), None, ctypes.byref(ent), None, None, _NO_UI, ctypes.byref(out)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))

    def protect(data: bytes) -> bytes:
        return _run(_crypt32.CryptProtectData, data)

    def unprotect(data: bytes) -> bytes:
        return _run(_crypt32.CryptUnprotectData, data)
else:
    def protect(data: bytes) -> bytes:
        return data

    def unprotect(data: bytes) -> bytes:
        return data


def enc(text: str) -> bytes:
    return protect(text.encode("utf-8")) if text else b""


def dec(data: bytes) -> str:
    return unprotect(bytes(data)).decode("utf-8") if data else ""
