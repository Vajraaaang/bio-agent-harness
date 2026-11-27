"""Confine filesystem execution to the pinned image before evaluating a script."""

import ctypes
import os
import platform
import shutil
import struct
import sys

# Linux UAPI <linux/landlock.h>, available on both supported OCI architectures.
_CREATE_RULESET = 444
_ADD_RULE = 445
_RESTRICT_SELF = 446
_EXECUTE = 1
_MAKE_CHAR = 1 << 6
_MAKE_SOCK = 1 << 9
_MAKE_FIFO = 1 << 10
_MAKE_BLOCK = 1 << 11


def confine():
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "aarch64"}:
        raise RuntimeError("Unsupported isolation kernel")
    libc = ctypes.CDLL(None, use_errno=True)

    def checked(result):
        if result < 0:
            raise OSError(ctypes.get_errno(), "Guest output isolation failed")
        return result

    # no_new_privs is also requested and verified by the host at container create.
    checked(libc.prctl(38, 1, 0, 0, 0))
    rights = ctypes.c_uint64(
        _EXECUTE | _MAKE_CHAR | _MAKE_BLOCK | _MAKE_SOCK | _MAKE_FIFO
    )
    ruleset = checked(
        libc.syscall(_CREATE_RULESET, ctypes.byref(rights), ctypes.sizeof(rights), 0)
    )
    try:
        # Only the immutable image can supply executables. The two bind mounts
        # and scratch cannot supply executable files or create device nodes.
        for directory in {
            os.path.realpath(p) for p in ("/usr", "/bin", "/lib", "/lib64", "/opt")
        }:
            if not os.path.isdir(directory):
                continue
            parent = os.open(directory, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                rule = ctypes.create_string_buffer(struct.pack("=Qi", _EXECUTE, parent))
                checked(libc.syscall(_ADD_RULE, ruleset, 1, ctypes.byref(rule), 0))
            finally:
                os.close(parent)
        checked(libc.syscall(_RESTRICT_SELF, ruleset, 0))
    finally:
        os.close(ruleset)


def main():
    confine()
    command = sys.argv[1:]
    program = shutil.which(command[0])
    if program is None:
        raise RuntimeError("Pinned interpreter unavailable")
    os.execv(program, command)


if __name__ == "__main__":
    main()
