#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Host regression tests; pass the original CFE RAM binary with --cferam."""

import argparse
import hashlib
import importlib.util
import lzma
import os
from pathlib import Path
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zlib

sys.dont_write_bytecode = True

TOP = Path(__file__).resolve().parents[4]
TARGET = TOP / "target/linux/bcmbca"
HOST = TOP / "staging_dir/host"
spec = importlib.util.spec_from_file_location("image", TARGET / "image/bcm6846-image.py")
image = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image)


class BringupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="bcmbca-test-")
        cls.work = Path(cls.temp.name)
        cls.cferam = CFERAM.read_bytes()
        if hashlib.sha256(cls.cferam).hexdigest() != image.CFERAM_SHA256:
            raise ValueError("wrong CFE RAM fixture")
        for utility in ["bcmbca-nvram", "bcmbca-bootcheck"]:
            subprocess.run([os.environ.get("CC", "cc"), "-Wall", "-Wextra", "-Werror",
                            "-O2", "-I" + str(HOST / "include"),
                            str(TOP / f"package/utils/bcmbca-utils/src/{utility}.c"),
                            str(HOST / "lib/libz.a"), "-o", str(cls.work / utility)], check=True)
        cls.dtb = struct.pack(">10I", 0xd00dfeed, 40, 0, 0, 0, 17, 16, 0, 0, 0)
        kernel = bytearray(256)
        kernel[36:40] = b"\x18\x28\x6f\x01"
        cls.kernel = kernel + cls.dtb
        (cls.work / "kernel").write_bytes(cls.kernel)
        (cls.work / "board.dtb").write_bytes(cls.dtb)
        cls.bundle = cls.work / "bundle"
        image.boot_bundle(argparse.Namespace(cferam=CFERAM, kernel=cls.work / "kernel",
                          dtb=cls.work / "board.dtb", output=cls.bundle))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_tool(self, tool, path, *args):
        return subprocess.run([str(self.work / tool), str(path), *map(str, args)],
                              capture_output=True, text=True)

    def nvram(self, count=16, mac=bytes.fromhex("44d43778c6a0")):
        data = bytearray(1024)
        struct.pack_into("<I", data, 0, 6)
        data[260:268] = b"XG6846B\0"
        struct.pack_into("<I", data, 284, count)
        data[288:294] = mac
        struct.pack_into("<I", data, 1020, zlib.crc32(data) ^ 0xffffffff)
        return data

    def nvram_file(self, primary, mirror=None):
        data = bytearray(0x20408)
        data[0x10580:0x10980] = primary
        if mirror is not None:
            data[0x20000:0x20008] = b"nVrAmDaT"
            data[0x20008:0x20408] = mirror
        path = self.work / "loader"
        path.write_bytes(data)
        return path

    def test_mac_pool(self):
        path = self.nvram_file(self.nvram())
        for port in range(5):
            result = self.run_tool("bcmbca-nvram", path, port)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), f"44:d4:37:78:c6:a{port}")
        self.assertNotEqual(self.run_tool("bcmbca-nvram", path, 16).returncode, 0)

    def test_nvram_mirror_and_invalid_crc(self):
        broken = self.nvram()
        broken[300] ^= 1
        path = self.nvram_file(broken, self.nvram())
        self.assertEqual(self.run_tool("bcmbca-nvram", path, 0).returncode, 0)
        path = self.nvram_file(broken, broken)
        self.assertNotEqual(self.run_tool("bcmbca-nvram", path, 0).returncode, 0)

    def test_preinit_port_names_and_macs(self):
        loader = self.nvram_file(self.nvram())
        net = self.work / "mock-net"
        net.mkdir(exist_ok=True)
        for port in ["eth0", "eth1", "eth2", "eth3", "eth4", "wan", "sfp"]:
            (net / port).mkdir(exist_ok=True)
        script = (TARGET / "base-files/lib/preinit/05_bcmbca_network").read_text()
        script = script.replace("/sys/class/net/", str(net) + "/")
        preinit = self.work / "preinit.sh"
        preinit.write_text(script)
        log = self.work / "network.log"
        log.write_text("")
        mock = r'''
board_name() { echo genexis,xg6846b; }
find_mtd_chardev() { echo "$MOCK_LOADER"; }
boot_hook_add() { :; }
ip() {
    echo "$*" >> "$MOCK_LOG"
    if [ "$5" = name ]; then
        mv "$MOCK_NET/$4" "$MOCK_NET/$6"
    fi
}
. "$1"
bcmbca_preinit_network
bcmbca_preinit_network
'''
        environment = dict(os.environ, PATH=str(self.work) + ":" + os.environ["PATH"],
                           MOCK_LOADER=str(loader), MOCK_NET=str(net), MOCK_LOG=str(log))
        result = subprocess.run(["sh", "-c", mock, "test", str(preinit)],
                                env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = log.read_text().splitlines()
        self.assertEqual(commands[:3], ["link set dev eth4 down",
                                        "link set dev eth4 name wan-cpu",
                                        "link set dev wan name eth4"])
        addresses = [f"link set dev eth{i} address 44:d4:37:78:c6:a{i}" for i in range(5)]
        addresses.append("link set dev wan-cpu address 44:d4:37:78:c6:a4")
        addresses.append("link set dev sfp address 44:d4:37:78:c6:a4")
        self.assertEqual(commands[3:], addresses * 2)

    def test_mac_limits_and_oui(self):
        for count in [0, 65]:
            path = self.nvram_file(self.nvram(count=count))
            self.assertNotEqual(self.run_tool("bcmbca-nvram", path, 0).returncode, 0)
        for mac in [bytes(6), bytes.fromhex("45d43778c6a0")]:
            path = self.nvram_file(self.nvram(mac=mac))
            self.assertNotEqual(self.run_tool("bcmbca-nvram", path, 0).returncode, 0)
        path = self.nvram_file(self.nvram(mac=bytes.fromhex("44d437ffffff")))
        self.assertEqual(self.run_tool("bcmbca-nvram", path, 1).stdout.strip(),
                         "44:d4:37:00:00:00")

    def test_boot_records_and_kernel(self):
        self.assertEqual(self.run_tool("bcmbca-bootcheck", self.bundle).returncode, 0)
        data = self.bundle.read_bytes()
        files = {}
        pos = 0
        while any(data[pos:pos + 16]):
            total, relative, length, checksum = struct.unpack_from(">4I", data, pos)
            self.assertEqual(checksum, zlib.crc32(data[pos:pos + 12]) ^ 0xffffffff)
            start = pos + 8 + relative
            self.assertEqual(struct.unpack_from(">I", data, pos + total)[0],
                             zlib.crc32(data[pos + 16:pos + total]) ^ 0xffffffff)
            name = data[pos + 16:start].rstrip(b"\0").decode()
            files[name] = data[start:start + length]
            pos += total + 4
        self.assertEqual(files["cferam.000"], self.cferam)
        self.assertEqual(files["96846.dtb"], self.dtb)
        header = struct.unpack("<III4sI", files["vmlinux.lz"][:20])
        self.assertEqual(header[:2], (0x200000, 0x200000))
        self.assertEqual(lzma.decompress(files["vmlinux.lz"][20:]), self.kernel)

    def test_bad_boot_bundles(self):
        data = self.bundle.read_bytes()
        path = self.work / "bad-bundle"
        for length in [0, 15, 16, 100, len(data) - 1]:
            path.write_bytes(data[:length])
            self.assertNotEqual(self.run_tool("bcmbca-bootcheck", path).returncode, 0)
        for offset in [0, 4, 8, 12, 16, 1000, len(data) - 1]:
            damaged = bytearray(data)
            damaged[offset] ^= 0xff
            path.write_bytes(damaged)
            self.assertNotEqual(self.run_tool("bcmbca-bootcheck", path).returncode, 0)

    def test_ubi_and_y3(self):
        root = self.work / "root-dir"
        root.mkdir(exist_ok=True)
        (root / "fixture").write_text("format test, not a bootable OpenWrt rootfs\n")
        rootfs = self.work / "root.ubifs"
        subprocess.run([str(HOST / "bin/mkfs.ubifs"), "-m", "2048", "-e", "126976",
                        "-c", "1880", "-r", str(root), "-o", str(rootfs)], check=True)
        ubi = self.work / "image.ubi"
        image.ubi_image(argparse.Namespace(rootfs=rootfs, kernel=self.bundle,
                        ubinize=str(HOST / "bin/ubinize"), output=ubi))
        tables = []
        data = ubi.read_bytes()
        for offset in range(0, len(data), 131072):
            peb = data[offset:offset + 131072]
            self.assertEqual(peb[:4], b"UBI#")
            vid = peb[2048:2112]
            vol_id = struct.unpack_from(">I", vid, 8)[0]
            self.assertEqual(struct.unpack_from(">I", vid, 60)[0],
                             zlib.crc32(vid[:60]) ^ 0xffffffff)
            if vol_id == 0x7fffefff:
                volumes = {}
                for index in range(128):
                    rec = peb[4096 + index * 172:4096 + (index + 1) * 172]
                    if struct.unpack_from(">I", rec)[0]:
                        volumes[index] = (rec[16:144].split(b"\0")[0].decode(), rec[12], rec[144])
                tables.append(volumes)
        expected = {0: ("rootfs_0", 1, 1), 1: ("METADATA", 1, 0),
                    2: ("METADATACOPY", 1, 0), 10: ("kernel_0", 2, 0)}
        self.assertEqual(tables, [expected, expected])
        y3 = self.work / "factory.y3"
        subprocess.run([str(HOST / "bin/y3-image"), "-i", str(ubi), "-o", str(y3),
                        "-c", "6846", "-m", "PANTHER", "-a", "all arm_xscale",
                        "-r", "OpenWrt-test"], check=True)
        data = y3.read_bytes()
        self.assertEqual(data[1024:-32], ubi.read_bytes())
        self.assertEqual(data[-32:], hashlib.md5(data[:-32]).hexdigest().encode())
        self.assertIn(b"\ncfe 0\n", data[:1024])
        self.assertIn(b"\nrescue 0\n", data[:1024])

    def upgrade_fixture(self):
        root = self.work / "mock-sysfs"
        root.mkdir(exist_ok=True)
        for index, name, kind, count in [(0, "rootfs_0", "dynamic", 1800),
                                         (1, "METADATA", "dynamic", 1),
                                         (2, "METADATACOPY", "dynamic", 1),
                                         (10, "kernel_0", "static", 128)]:
            volume = root / f"ubi0_{index}"
            volume.mkdir(exist_ok=True)
            for field, value in [("name", name), ("type", kind),
                                 ("reserved_ebs", count), ("usable_eb_size", 126976)]:
                (volume / field).write_text(str(value) + "\n")
        directory = self.work / "sysupgrade-genexis_xg6846b"
        directory.mkdir(exist_ok=True)
        (directory / "CONTROL").write_text("BOARD=genexis_xg6846b\n")
        (directory / "kernel").write_bytes(self.bundle.read_bytes())
        (directory / "root").write_bytes(b"\x31\x18\x10\x06" + bytes(4092))
        archive = self.work / "sysupgrade.bin"
        with tarfile.open(archive, "w", format=tarfile.USTAR_FORMAT) as tar:
            tar.add(directory, arcname=directory.name)
        script = (TARGET / "base-files/lib/upgrade/platform.sh").read_text()
        script = script.replace("/sys/class/ubi/", str(root) + "/")
        script = script.replace("/tmp/bcmbca-upgrade", str(self.work / "upgrade"))
        (self.work / "platform.sh").write_text(script)
        # Use OpenWrt's actual format helpers, including their input-command API.
        upgrade = TOP / "package/base-files/files/lib/upgrade"
        (self.work / "common.sh").write_text((upgrade / "common.sh").read_text())
        (self.work / "nand.sh").write_text((upgrade / "nand.sh").read_text().replace(
            ". /lib/functions.sh", ""))
        return root, archive

    def mock_upgrade(self, archive, command, fail=""):
        log = self.work / "writes.log"
        log.write_text("")
        script = r'''
. "$MOCK_HELPERS/common.sh"
. "$MOCK_HELPERS/nand.sh"
board_name() { echo genexis,xg6846b; }
nand_find_ubi() { echo ubi0; }
ubi_mknod() { :; }
ubiupdatevol() {
    echo "$1" >> "$MOCK_LOG"
    [ "$1" != "$MOCK_FAIL" ]
}
nand_do_restore_config() { echo restore >> "$MOCK_LOG"; }
sync() { :; }
. "$1"
"$3" "$2"
'''
        environment = dict(os.environ, PATH=str(self.work) + ":" + os.environ["PATH"],
                           MOCK_LOG=str(log), MOCK_FAIL=fail, MOCK_HELPERS=str(self.work))
        result = subprocess.run(["sh", "-c", script, "test", str(self.work / "platform.sh"),
                                 str(archive), command], env=environment,
                                capture_output=True, text=True)
        return result, log.read_text().splitlines()

    def test_sysupgrade_and_config_restore(self):
        _, archive = self.upgrade_fixture()
        result, writes = self.mock_upgrade(archive, "platform_check_image")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(writes, [])
        result, writes = self.mock_upgrade(archive, "bcmbca_upgrade")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(writes, ["/dev/ubi0_0", "/dev/ubi0_10", "restore"])

    def test_sysupgrade_write_failures(self):
        _, archive = self.upgrade_fixture()
        for failure, expected in [("/dev/ubi0_0", ["/dev/ubi0_0"]),
                                  ("/dev/ubi0_10", ["/dev/ubi0_0", "/dev/ubi0_10"])]:
            result, writes = self.mock_upgrade(archive, "bcmbca_upgrade", failure)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(writes, expected)

    def test_sysupgrade_rejects_layout_and_size(self):
        root, archive = self.upgrade_fixture()
        (root / "ubi0_10/reserved_ebs").write_text("1\n")
        result, writes = self.mock_upgrade(archive, "bcmbca_upgrade")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(writes, [])
        (root / "ubi0_10/reserved_ebs").write_text("128\n")
        (root / "ubi0_10/name").write_text("kernel_1\n")
        result, writes = self.mock_upgrade(archive, "platform_check_image")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(writes, [])
        (root / "ubi0_10/name").write_text("kernel_0\n")
        extra = root / "ubi0_89"
        extra.mkdir()
        (extra / "name").write_text("rootfs_1\n")
        result, writes = self.mock_upgrade(archive, "platform_check_image")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(writes, [])
        (extra / "name").unlink()
        extra.rmdir()

    def test_sysupgrade_rejects_non_tar(self):
        self.upgrade_fixture()
        result, writes = self.mock_upgrade(self.bundle, "platform_check_image")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(writes, [])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cferam", type=Path, required=True)
    args, remaining = parser.parse_known_args()
    CFERAM = args.cferam.resolve()
    unittest.main(argv=[__file__, *remaining])
