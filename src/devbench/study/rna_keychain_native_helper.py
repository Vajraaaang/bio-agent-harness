"""Internal native helper: exact default-Keychain item to a private stdout pipe.

No package/site imports, selector options, HTTP, environment credentials, files,
or diagnostic text. Only the admitted exporter starts this supervised helper.
Same-UID manual access is not an OS sandbox or authenticated operator boundary.
"""
import ctypes
import os
import stat
import sys

PYTHON='/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.8/bin/python3.8'
SERVICE=b'bio-agent-harness.langsmith'
ACCOUNT=b'bio-harness'


def _runtime_valid():
    allowed={'PATH','__CF_USER_TEXT_ENCODING','LC_CTYPE','__PYVENV_LAUNCHER__'}
    return bool(len(sys.argv)==1 and sys.platform=='darwin' and sys.executable==PYTHON
        and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
        and set(os.environ)<=allowed and os.environ.get('PATH')=='/usr/bin:/bin'
        and os.environ.get('LC_CTYPE')=='C.UTF-8'
        and os.environ.get('__PYVENV_LAUNCHER__')==PYTHON
        and stat.S_ISFIFO(os.fstat(1).st_mode))


def _read_exact_item():
    security=ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
    core=ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    security.SecKeychainCopyDefault.argtypes=[ctypes.POINTER(ctypes.c_void_p)]
    security.SecKeychainCopyDefault.restype=ctypes.c_int32
    security.SecKeychainFindGenericPassword.argtypes=[ctypes.c_void_p,ctypes.c_uint32,
        ctypes.c_void_p,ctypes.c_uint32,ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p]
    security.SecKeychainFindGenericPassword.restype=ctypes.c_int32
    security.SecKeychainItemFreeContent.argtypes=[ctypes.c_void_p,ctypes.c_void_p]
    security.SecKeychainItemFreeContent.restype=ctypes.c_int32
    core.CFRelease.argtypes=[ctypes.c_void_p];core.CFRelease.restype=None
    handle,content=ctypes.c_void_p(),ctypes.c_void_p()
    length=ctypes.c_uint32()
    try:
        if security.SecKeychainCopyDefault(ctypes.byref(handle))!=0 or not handle:
            return 22,None
        code=security.SecKeychainFindGenericPassword(handle,len(SERVICE),SERVICE,
            len(ACCOUNT),ACCOUNT,ctypes.byref(length),ctypes.byref(content),None)
        if code!=0:
            return {-25300:23,-25293:24,-25308:25}.get(code,26),None
        if not content or not 1<=length.value<=256:
            return 27,None
        value=ctypes.string_at(content,length.value)
        if any(c<33 or c>126 for c in value):return 27,None
        return 0,value
    finally:
        if content:security.SecKeychainItemFreeContent(None,content)
        if handle:core.CFRelease(handle)


def main():
    try:
        if not _runtime_valid():return 20
        code,value=_read_exact_item()
        if code==0:
            sys.stdout.buffer.write(value+b'\n')
            sys.stdout.buffer.flush()
        return code
    except Exception:
        return 21


if __name__=='__main__':raise SystemExit(main())
