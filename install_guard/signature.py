"""数字签名验证、文件哈希与版本信息读取。"""
from __future__ import annotations

import ctypes
import hashlib
import os
from ctypes import wintypes

WTD_UI_NONE = 2
WTD_REVOKE_NONE = 0
WTD_CHOICE_FILE = 1
WTD_STATEACTION_VERIFY = 1
WTD_STATEACTION_CLOSE = 2
WTD_CACHE_ONLY_URL_RETRIEVAL = 0x1000


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __init__(self, text: str) -> None:
        parts = text.strip("{}").split("-")
        self.Data1 = int(parts[0], 16)
        self.Data2 = int(parts[1], 16)
        self.Data3 = int(parts[2], 16)
        for index, byte in enumerate(bytes.fromhex(parts[3] + parts[4])):
            self.Data4[index] = byte


class _FileInfo(ctypes.Structure):
    _fields_ = [
        ("cbStruct", wintypes.DWORD),
        ("lpwszFilePath", wintypes.LPCWSTR),
        ("hFile", wintypes.HANDLE),
        ("pgKnownSubject", ctypes.c_void_p),
    ]


class _Union(ctypes.Union):
    _fields_ = [
        ("pFile", ctypes.POINTER(_FileInfo)),
        ("raw", ctypes.c_byte * ctypes.sizeof(_FileInfo)),
    ]


class _TrustData(ctypes.Structure):
    _fields_ = [
        ("cbStruct", wintypes.DWORD),
        ("pPolicyCallbackData", ctypes.c_void_p),
        ("pSIPClientData", ctypes.c_void_p),
        ("dwUIChoice", wintypes.DWORD),
        ("fdwRevocationChecks", wintypes.DWORD),
        ("dwUnionChoice", wintypes.DWORD),
        ("union", _Union),
        ("dwStateAction", wintypes.DWORD),
        ("hWVTStateData", wintypes.HANDLE),
        ("pwszURLReference", wintypes.LPCWSTR),
        ("dwProvFlags", wintypes.DWORD),
        ("dwUIContext", wintypes.DWORD),
        ("pSignatureSettings", ctypes.c_void_p),
    ]


_wintrust = ctypes.WinDLL("wintrust", use_last_error=True)
_wintrust.WinVerifyTrust.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(_GUID),
    ctypes.POINTER(_TrustData),
]
_wintrust.WinVerifyTrust.restype = wintypes.LONG

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_wintrust.CryptCATAdminAcquireContext.argtypes = [
    ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, wintypes.DWORD
]
_wintrust.CryptCATAdminAcquireContext.restype = wintypes.BOOL
_wintrust.CryptCATAdminCalcHashFromFileHandle.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, wintypes.DWORD
]
_wintrust.CryptCATAdminCalcHashFromFileHandle.restype = wintypes.BOOL
_wintrust.CryptCATAdminEnumCatalogFromHash.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p
]
_wintrust.CryptCATAdminEnumCatalogFromHash.restype = ctypes.c_void_p
_wintrust.CryptCATAdminReleaseCatalogContext.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD
]
_wintrust.CryptCATAdminReleaseCatalogContext.restype = wintypes.BOOL
_wintrust.CryptCATAdminReleaseContext.argtypes = [ctypes.c_void_p, wintypes.DWORD]
_wintrust.CryptCATAdminReleaseContext.restype = wintypes.BOOL
_kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
_kernel32.CreateFileW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL

_ACTION = _GUID("{00AAC56B-CD44-11D0-8CC2-00C04FC295EE}")
_INVALID_HANDLE = ctypes.c_void_p(-1).value


def verify_embedded(path: str) -> bool:
    """检查文件内嵌的 Authenticode 签名；无网络行为。"""
    if not path or not os.path.isfile(path):
        return False
    file_info = _FileInfo()
    file_info.cbStruct = ctypes.sizeof(_FileInfo)
    file_info.lpwszFilePath = os.path.abspath(path)

    data = _TrustData()
    data.cbStruct = ctypes.sizeof(_TrustData)
    data.dwUIChoice = WTD_UI_NONE
    data.fdwRevocationChecks = WTD_REVOKE_NONE
    data.dwUnionChoice = WTD_CHOICE_FILE
    data.union.pFile = ctypes.pointer(file_info)
    data.dwStateAction = WTD_STATEACTION_VERIFY
    data.dwProvFlags = 0x10 | WTD_CACHE_ONLY_URL_RETRIEVAL
    data.dwUIContext = 0

    try:
        status = _wintrust.WinVerifyTrust(None, ctypes.byref(_ACTION), ctypes.byref(data))
        return status == 0
    except OSError:
        return False
    finally:
        data.dwStateAction = WTD_STATEACTION_CLOSE
        try:
            _wintrust.WinVerifyTrust(None, ctypes.byref(_ACTION), ctypes.byref(data))
        except OSError:
            pass


def verify_catalog(path: str) -> bool:
    """检查文件是否属于已签名的系统目录（Windows 系统文件多为目录签名）。"""
    if not path or not os.path.isfile(path):
        return False
    cat_admin = ctypes.c_void_p()
    if not _wintrust.CryptCATAdminAcquireContext(ctypes.byref(cat_admin), None, 0):
        return False
    try:
        handle = _kernel32.CreateFileW(
            os.path.abspath(path), 0x80000000, 0x1, None, 3, 0, None
        )
        if not handle or handle == _INVALID_HANDLE:
            return False
        try:
            size = wintypes.DWORD(0)
            if not _wintrust.CryptCATAdminCalcHashFromFileHandle(
                handle, ctypes.byref(size), None, 0
            ) or not size.value:
                return False
            digest = (ctypes.c_ubyte * size.value)()
            if not _wintrust.CryptCATAdminCalcHashFromFileHandle(
                handle, ctypes.byref(size), digest, 0
            ):
                return False
            catalog = _wintrust.CryptCATAdminEnumCatalogFromHash(
                cat_admin, digest, size, 0, None
            )
            if not catalog:
                return False
            _wintrust.CryptCATAdminReleaseCatalogContext(cat_admin, catalog, 0)
            return True
        finally:
            _kernel32.CloseHandle(handle)
    except OSError:
        return False
    finally:
        _wintrust.CryptCATAdminReleaseContext(cat_admin, 0)


def verify(path: str) -> bool:
    """内嵌签名或系统目录签名任一有效，即视为已签名。"""
    return verify_embedded(path) or verify_catalog(path)


def file_hash(path: str) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def version_info(path: str) -> dict[str, str]:
    """从 PE 版本资源读取公司名/产品名，用作发行者展示与白名单依据。"""
    result = {"company": "", "product": "", "description": "", "version": ""}
    try:
        import win32api

        fixed = win32api.GetFileVersionInfo(path, "\\")
        translations = fixed.get("VarFileInfo", {}).get("Translation", [])
        lang, codepage = translations[0] if translations else (0x0409, 0x04B0)
        block = "\\StringFileInfo\\%04x%04x\\" % (lang, codepage)
        for key, field in (
            ("CompanyName", "company"),
            ("ProductName", "product"),
            ("FileDescription", "description"),
        ):
            try:
                result[field] = win32api.GetFileVersionInfo(path, block + key).strip()
            except Exception:
                pass
        ms = fixed.get("FileVersionMS", 0)
        ls = fixed.get("FileVersionLS", 0)
        result["version"] = "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except Exception:
        pass
    return result
