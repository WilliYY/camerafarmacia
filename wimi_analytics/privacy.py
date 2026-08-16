import ctypes
import os
from ctypes import wintypes


class DataProtectionError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _blob_from_bytes(value):
    buffer = ctypes.create_string_buffer(value)
    blob = _DataBlob(
        len(value),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
    )
    return blob, buffer


def _run_dpapi(function_name, value, description=None):
    if os.name != "nt":
        raise DataProtectionError("dpapi_requires_windows")
    if not isinstance(value, bytes) or not value:
        raise DataProtectionError("invalid_protected_payload")

    source, source_buffer = _blob_from_bytes(value)
    destination = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    function = getattr(crypt32, function_name)
    description_pointer = wintypes.LPWSTR()
    ctypes.set_last_error(0)
    if function_name == "CryptProtectData":
        ok = function(
            ctypes.byref(source),
            description or "WIMI local biometric profile",
            None,
            None,
            None,
            0,
            ctypes.byref(destination),
        )
    else:
        ok = function(
            ctypes.byref(source),
            ctypes.byref(description_pointer),
            None,
            None,
            None,
            0,
            ctypes.byref(destination),
        )
    error_code = ctypes.get_last_error()
    if not ok:
        if description_pointer:
            kernel32.LocalFree(ctypes.cast(description_pointer, wintypes.HLOCAL))
        raise DataProtectionError(f"{function_name}_failed_{error_code}")
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        if description_pointer:
            kernel32.LocalFree(ctypes.cast(description_pointer, wintypes.HLOCAL))
        if destination.pbData:
            kernel32.LocalFree(ctypes.cast(destination.pbData, wintypes.HLOCAL))


def protect_bytes(value):
    return _run_dpapi("CryptProtectData", value)


def unprotect_bytes(value):
    return _run_dpapi("CryptUnprotectData", value)


class DpapiProtector:
    def protect(self, value):
        return protect_bytes(value)

    def unprotect(self, value):
        return unprotect_bytes(value)
