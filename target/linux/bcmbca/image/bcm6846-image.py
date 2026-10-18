#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build the legacy BCM6846 CFE boot records and four-volume UBI layout.

Y3 packaging belongs to firmware-utils' y3-image, not this helper.
"""

import argparse
import hashlib
import lzma
from pathlib import Path
import struct
import subprocess
import tempfile
import zlib


CFERAM_SHA256 = "4c68fd0a16b4b87bb8e46719e4751f0055c0217fce604ee90bf7a1c9f6852c2e"
LEB_SIZE = 126976
KERNEL_LEBS = 128


def crc(data):
    return zlib.crc32(data) ^ 0xffffffff


def records(files):
    result = bytearray()
    for name, data in files:
        name = name.encode("ascii") + b"\0"
        name += bytes(-len(name) % 4)
        header = struct.pack(">III", 16 + len(name) + len(data),
                             8 + len(name), len(data))
        result += header + struct.pack(">I", crc(header))
        result += name + data + struct.pack(">I", crc(name + data))
    return result + bytes(16)


def boot_bundle(args):
    cferam = args.cferam.read_bytes()
    if hashlib.sha256(cferam).hexdigest() != CFERAM_SHA256:
        raise ValueError("CFERAM is not the supported XG6846B 1.0.38-164.255-IOP2.2 binary")
    kernel = args.kernel.read_bytes()
    if len(kernel) < 48 or kernel[36:40] != b"\x18\x28\x6f\x01":
        raise ValueError("expected an ARM zImage with appended board DTB")
    dtb = args.dtb.read_bytes()
    if not dtb.startswith(b"\xd0\x0d\xfe\xed") or not kernel.endswith(dtb):
        raise ValueError("kernel must end with the supplied board DTB")
    compressed = lzma.compress(kernel, format=lzma.FORMAT_ALONE, filters=[{
        "id": lzma.FILTER_LZMA1, "dict_size": 1 << 22,
        "lc": 1, "lp": 2, "pb": 2,
    }])
    # CFE's LZMA-alone decoder expects the actual uncompressed length.
    compressed = compressed[:5] + struct.pack("<Q", len(kernel)) + compressed[13:]
    header = struct.pack("<III4sI", 0x00200000, 0x00200000,
                         len(compressed), b"BRCM", 0)
    result = records([("cferam.000", cferam),
                      ("vmlinux.lz", header + compressed), ("96846.dtb", dtb)])
    if len(result) > KERNEL_LEBS * LEB_SIZE:
        raise ValueError("boot bundle exceeds the fixed kernel_0 reservation")
    args.output.write_bytes(result)


def ubi_image(args):
    if args.rootfs.read_bytes()[:4] != b"\x31\x18\x10\x06":
        raise ValueError("rootfs_0 must contain UBIFS")
    metadata = records([("committed", b"1"), ("cferam.000", b"999")])
    with tempfile.TemporaryDirectory(prefix="bcmbca-", dir=args.output.parent) as directory:
        tmp = Path(directory)
        (tmp / "metadata").write_bytes(metadata)
        sections = []
        for vol_id, name, kind, image, extra in [
            (0, "rootfs_0", "dynamic", args.rootfs, "vol_flags=autoresize\n"),
            (1, "METADATA", "dynamic", tmp / "metadata", f"vol_size={LEB_SIZE}\n"),
            (2, "METADATACOPY", "dynamic", tmp / "metadata", f"vol_size={LEB_SIZE}\n"),
            (10, "kernel_0", "static", args.kernel,
             f"vol_size={KERNEL_LEBS * LEB_SIZE}\n"),
        ]:
            sections.append(f"[{name}]\nmode=ubi\nimage={image.resolve()}\n"
                            f"vol_id={vol_id}\nvol_type={kind}\nvol_name={name}\n{extra}\n")
        config = tmp / "ubinize.ini"
        config.write_text("".join(sections))
        subprocess.run([args.ubinize, "-o", str(args.output), "-p", "131072",
                        "-m", "2048", "-O", "2048", "-Q", "6846", str(config)],
                       check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    boot = commands.add_parser("boot")
    boot.add_argument("--kernel", type=Path, required=True)
    boot.add_argument("--dtb", type=Path, required=True)
    boot.add_argument("--cferam", type=Path, required=True)
    boot.add_argument("--output", type=Path, required=True)
    ubi = commands.add_parser("ubi")
    ubi.add_argument("--kernel", type=Path, required=True)
    ubi.add_argument("--rootfs", type=Path, required=True)
    ubi.add_argument("--ubinize", required=True)
    ubi.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        (boot_bundle if args.command == "boot" else ubi_image)(args)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"bcm6846-image: {error}\n")


if __name__ == "__main__":
    main()
