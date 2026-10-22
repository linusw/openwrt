# BCM6846 / Genexis XG6846B bring-up

This target uses the BCM6846 CFE SMP, PMC/PMB and reduced XRDP Ethernet
series from `linux-integrator`, branch `b4/genexis-xg6846b-ethernet`, through
commit `b00f3b715cdb`, based on v7.3-rc1 and rebased onto Linux 6.18.55.
This includes the shared MFD PMB transport, reset and clock providers,
PVTMON temperature/voltage monitoring, watchdog restart support, and the
Broadcom internal PHY driver. The BCM6846 I2C node is also backported. The Ethernet
driver uses polling and SRAM copies; this is initial support without the
vendor packet acceleration. The SFP network port is enabled using the
source series' Marvell 88E6320 PCS support. The 6.18 stack also backports
the seven upstream scratch-register and 88E6321 SerDes prerequisites
that this support reuses. The source separates the SoC XRDP description
from the board Ethernet setup and includes the tested GPIO 40 SFP enable
in the switch/SFP patch.
The previously tested XRDP power-up, PHY block power and RTNL fixes are now
part of the consolidated driver patches. The OpenWrt flash layout and
console-preserving UART shutdown patch remain separate.

The CFE SMP prerequisites use the explicit `brcm,bca-smp` CPU enable
method and include its binding. CPU1's `cpu-release-addr` still points to
the CFE mailbox at `0xff800568`. The board SMP patch also preserves the
holding pen by removing `no-map` from its reservation.

The PMB clock driver uses distinct RDP and BIU PLL register layouts and
checks their hardware lock indication without interpreting firmware-owned
power/reset controls. Zero dividers report an unknown rate.

XRDP holds the PMB clocks while its hardware is accessible and releases
them after stopping the datapath. PVTMON exposes temperature and seven
voltage inputs through hwmon, with temperature also available to the DT
thermal zone. On hardware, inspect these with:

```
cat /sys/class/hwmon/hwmon*/name
cat /sys/class/hwmon/hwmon*/temp1_input
cat /sys/class/hwmon/hwmon*/in*_input
cat /sys/class/thermal/thermal_zone*/temp
```

Values use millidegrees Celsius and millivolts. The hardware results below
refer to the earlier images; the refreshed clock and monitoring support
still needs testing on the router.

## Building

Select `Broadcom Broadband Carrier Access`, `BCM6846 (Cortex-A7)`, and
`Genexis XG6846B`. Enable UBIFS and optionally the initramfs image. Userspace
uses the soft-float ABI; the two CPUs must have a common instruction set.

The host firmware-utils installation must include `y3-image`. While the
firmware-utils change is pending, build `src/y3-image.c` with `src/md5.c`
from that repository and install the resulting host binary as
`staging_dir/host/bin/y3-image`. The Y3 utility only packages the completed
UBI image; it does not create filesystems or UBI volumes.

Copy the original board's extracted `kernel_0/cferam.000` to
`dl/genexis-xg6846b-cferam.000`, or pass `BCMBCA_CFERAM=/path/to/cferam.000`
to make. This is the CFE **RAM stage**, not the first NAND partition. Its
required SHA-256 is:

```
4c68fd0a16b4b87bb8e46719e4751f0055c0217fce604ee90bf7a1c9f6852c2e
```

This binary comes from CFE `1.0.38-164.255-IOP2.2` in the XG6846B NAND dump.
It is not included in this patch. The build deliberately fails for a
missing or different loader. Both factory and sysupgrade bundles retain it.

Output in `bin/targets/bcmbca/bcm6846/` includes:

* `*-ubifs-factory.y3`: legacy version-8 Y3 containing a complete new UBI.
* `*-ubifs-sysupgrade.bin`: OpenWrt sysupgrade tar with image metadata.
* `*-initramfs-zImage`: optional RAM boot image with appended board DTB.

The boot bundle contains `cferam.000`, `vmlinux.lz` and `96846.dtb` as
Broadcom CRC-protected file records. `vmlinux.lz` is an LZMA-wrapped ARM
zImage with an appended DTB and a BRCM header loading/entering at `0x00200000`.
The separate DTB is for CFE. The kernel forces its own root arguments because
the vendor CFE supplies `ubi.mtd=0`, which would select the loader partition
with the mainline partition numbering.

## Flash layout and upgrades

NAND uses 128 KiB eraseblocks, 2 KiB pages, a VID header at 2 KiB and data at
4 KiB. Linux marks `[0, 0x00400000)` read-only. UBI occupies
`[0x00400000, 0x0ff80000)`; the final four eraseblocks are reserved for BBTs.

| Volume ID | Name | Type | Reservation |
| ---: | --- | --- | --- |
| 0 | rootfs_0 | dynamic UBIFS | grows to available space |
| 1 | METADATA | dynamic | one LEB |
| 2 | METADATACOPY | dynamic | one LEB |
| 10 | kernel_0 | static | 128 LEBs, including CFE RAM |

There are no second-bank or `rootfs_data` volumes. UBI also creates its own
internal layout volume. The kernel reservation allows later kernel growth
without resizing or renumbering the volume table.

Factory installation requires the legacy CFE **whole-UBI** Y3 installer.
In the supplied CFE binary, this is reached from the recovery web interface:
the HTTP upload handler recognizes `IntenoIopY`, selects image type 100,
and dispatches to the Y3 parser at `0x00f131a4`. The embedded form is
`/upload.html`, posting to `upload.cgi`. With autoboot interrupted at the
CFE prompt, use the board IP printed by CFE (the supplied log shows
`192.168.1.1`) and a host on that subnet. Select the factory Y3, leave the
misc-partition selector blank, and use **Update Software**. Installation
through this interface and the subsequent CFE-to-Linux handoff have been
confirmed on the router. Serial `w`/`ws` commands have not been established
as Y3 installation paths.

The vendor Linux updater writes an alternate bank and does not establish
this layout. Do not install this factory image through that updater.

The Y3 has zero-length CFE ROM and rescue sections. CFE's installer retains
their executable contents but still rewrites NVRAM fields and their CRC in
the loader area. It does **not** leave the first 4 MiB byte-for-byte untouched.

After first installation, use normal OpenWrt `sysupgrade` with the
`*-sysupgrade.bin` file. Only that tar format and the expected fixed-ID UBI
layout are accepted. The updater checks the embedded CFE RAM stage, writes
rootfs_0 then kernel_0 using `ubiupdatevol`, and restores the configuration
backup into UBIFS. `sysupgrade -n` discards the old configuration. It does not
erase/reformat NAND, recreate volumes, modify the metadata, or write loader.
On failure, it opens a serial recovery shell in RAM instead of rebooting.

**There is only one boot bank. A failed or interrupted update of kernel_0
can remove the CFE RAM stage needed for recovery.** Keep the NAND backup and
a tested serial/ROM recovery method. This layout provides neither atomic
system upgrades nor a second firmware bank. Writable UBIFS also lacks the
immutable SquashFS base normally used by OpenWrt factory reset.

## Networking and NVRAM

`eth0` through `eth3` form `br-lan` (OpenWrt's default static LAN address and
DHCP server). Copper `eth4` and optical `sfp` form `br-wan`, with the
standard DHCP/DHCPv6 clients, firewall zones and IPv4 masquerading supplied
by the default router packages. They are bridged alternatives for the same
upstream network, not independent routed uplinks.

The upstream series registers the fifth XRDP port as `eth4`, with a DSA
user port named `wan`. Preinit renames the internal link to `wan-cpu` and
the copper port to `eth4`, before configuring networking. Never add
`wan-cpu` to the LAN bridge. The switch SFP port keeps its `sfp` name.

These defaults apply when OpenWrt generates a new network configuration.
Sysupgrade with preserved settings retains the previous WAN device. To
add SFP to the earlier default copper WAN configuration:

```
uci set network.wan_bridge=device
uci set network.wan_bridge.name=br-wan
uci set network.wan_bridge.type=bridge
uci add_list network.wan_bridge.ports=eth4
uci add_list network.wan_bridge.ports=sfp
uci set network.wan.device=br-wan
uci set network.wan6.device=br-wan
uci commit network
/etc/init.d/network restart
```

The NVRAM reader validates the 1024-byte v6 structure at loader offset
`0x10580`, with the `nVrAmDaT` mirror at `0x20000` as fallback. It checks
the board ID and CRC with the checksum field zeroed. The vendor
`bcm_hwdefs.h` places `ulNumMacAddrs` at byte 284 and `ucaBaseMacAddr` at
byte 288. `board_util.c:getNthMacAddr()` increments only the low 24 bits.
`enet.c:enet_dev_mac_set()` requests addresses from that pool; the stock
allocation depends on interface role and registration order.

OpenWrt initially assigns stable pool indices 0..4 to eth0..eth4 and uses
index 0 for the LAN bridge. The default LAN interface MAC setting makes
config_generate apply that same address to all four bridge members, so
their running addresses also use index 0. Copper WAN, SFP and their wan-cpu
conduit share index 4; the WAN bridge uses that same address. The reader
checks the pool size before allocating each address.
The supplied unit has a pool of 16. Invalid NVRAM is reported and never
written; the driver-provided address remains in place in that case.

## SFP module detection

GPIO 40 (bank gpio1, offset 8) is reserved as an output-high GPIO hog named
`sfp-enable`. GenXOS drives this pin high during normal operation and low
for optical blackout. A hardware test confirmed that leaving it as an
input causes EEPROM reads at address 0x50 to NACK; driving it high makes
the module return SFP identifier 0x03. The exact power-control circuitry
has not been established, so this is described as an enable line rather
than a regulator supply.

The GPIO hog applies the enable state when the GPIO controller registers,
before the SFP driver probes the module. The hardware I2C controller keeps
its existing `brcm,brcmper-i2c` configuration. The Marvell switch port is
now enabled with 1000BASE-X and in-band status. PCS link detection and
packet traffic through SFP still require hardware validation.
Hardware testing of the image built from commit `2397361d1b21` confirms
automatic module identification at boot: the SFP driver reports vendor
`OEM`, model `FTCS-1312-20D`, revision `11.0`. This validates the GPIO enable
and EEPROM access through the existing I2C driver. The warning about a
missing TX-disable pin remains; no TX-disable GPIO is described yet.

## LEDs

The target explicitly enables the `netdev` and `default-on` LED triggers,
matching linux-integrator commit `3d7d3011befa`. The board script
`/usr/sbin/xg6846b-leds.sh` is adapted from the XG6846B documentation stash
and runs with `/bin/sh`. Its `xg6846b-leds` init service runs automatically
at S95, after network setup and S95done, before S96led applies UCI LED
settings. It only runs on the XG6846B.

LAN1 through LAN4 follow eth0 through eth3, and the EXT LEDs follow the
physical copper WAN port, eth4, after the preinit rename. Green indicates
a 1 Gbit/s link and amber a 100 Mbit/s link, with RX/TX activity blinking
enabled on both colors. A target kernel patch makes software activity
blinking conditional on the selected link mode: at 1 Gbit/s only green
lights and blinks, and at 100 Mbit/s only amber lights and blinks. Link
events update the selection when speed changes or a cable is unplugged.
The green WAN LED (LED4, `green:wan`) follows `sfp`: it lights on link at
any speed and blinks with RX/TX activity. It is off while the SFP link is
down. The red power LED uses `default-on`. The kernel follows subsequent
link, speed and traffic changes without a userspace polling process. An
unsupported speed indicator is left off.

To reapply these defaults, run `/etc/init.d/xg6846b-leds restart`.
Custom UCI LED settings take precedence during normal boot; restart
`/etc/init.d/led` after manually reapplying the board defaults to restore
those settings.

## Hardware validation

Before installing the factory image, test the initramfs with the established
CFE -> custom U-Boot -> `loady` -> `go 0x00200000` path. This validates the
OpenWrt kernel/userspace but does not prove direct CFE flash boot. Record the
serial log, `/proc/cmdline`, `/proc/mtd`, `ubinfo -a`, `ip link show`, and
`ubus call system board`. Confirm both CPUs, all four LAN ports, copper WAN,
the provisioned LAN and WAN MACs, DHCP and routed/NAT traffic.
The embedded initramfs uses a console-only command line, leaving the vendor
UBI unattached during RAM bring-up; `ubinfo -a` should therefore find none.

The initial initramfs has passed hardware tests for both CPUs, all four LAN
ports, DHCP and routed traffic through eth4. With the XRDP power-up fix,
the factory image from commit `8d693fbc93d0` also boots directly from CFE
to OpenWrt userspace. Both CPUs start, all five Ethernet interfaces
register, and preinit assigns the copper WAN port to eth4.

On the tested board, UBI attaches the image partition with 2012 good PEBs,
no bad or corrupted PEBs, and 40 PEBs reserved for bad-block handling.
rootfs_0 grows from 44 to 1838 LEBs (222.5 MiB), completes UBIFS free-space
fixup, and mounts as root. `ubinfo -a` reports exactly the four volumes
listed above, all in state OK. Zero available UBI LEBs is expected after
autoresize: filesystem free space is inside rootfs_0 and shown by `df`.

A normal sysupgrade has completed on hardware, restored the configured
hostname, and rebooted through CFE into OpenWrt. The transfer succeeded over
the copper WAN port, eth4. The shutdown log exposed a missing RTNL lock
around PHY disconnection; the follow-up patch fixes both disconnect paths.
An initial normal reboot capture stopped at procd's SIGTERM message and
resumed at BTRM. The UART shutdown callback disabled its baud generator
and transmitter on tty hangup even for the kernel console. A follow-up
patch keeps console ports enabled while still masking their interrupts
and releasing the IRQ.

Commit `b393f94e7972` (image banner `r37388+10-64129c428221`) boots on
hardware, mounts rootfs_0 and restores the configured hostname. A separate
normal reboot now prints SIGTERM, SIGKILL, procd's reboot message and the
kernel's `Restarting system` message before returning through BTRM and
HELO to CFE's memory initialization. No PHY-disconnect RTNL warning occurs.
This verifies restored console output and the normal shutdown path with
the RTNL fix. LAN4/eth3 also negotiates 1 Gbit/s full duplex and enters
bridge forwarding during this boot.
A clean `sysupgrade -n` remains untested.

The initial flash image's LAN ports eth0 through eth3 did not establish
link, despite the earlier initramfs networking result.
They connect to the internal BCM6846 PHYs; the working eth4 path crosses the
Marvell switch. All four internal PHYs respond with ID `0xae0251e1`.
The BCM6846 PHY patch replaces the Generic PHY fallback with a software
reset, the vendor analog/DSP calibration, automatic pair crossover and
wirespeed. Hardware confirms that this driver attaches, but calibration
alone does not establish link. The shared QEGPHY control at `0x82db2208`
reads `0x0010105f`: bias/global IDDQ and all four port power-down bits are
set, even though each PHY's standard BMCR power-down bit is clear.

The follow-up patch powers the shared block after XRDP initialization and
before attaching any PHY, using the BCM6846 CFE reset/clock sequence under
the MDIO bus lock. It preserves the discovered PHY base address and powers
only enabled internal ports. For the measured initial state and all four
LAN ports enabled, the expected final control value is `0x00101000`.
With the shared power-up fix in commit `97359a5ea70d` (image banner
`r37388+6-64129c428221`), LAN1/eth0 now negotiates 1 Gbit/s full duplex,
reports carrier 1 and enters bridge forwarding. With eth4 administratively
down and an address assigned to br-lan, all 22 pings to the connected host
succeed. Follow-up cable and ping tests also pass on LAN2 through LAN4,
confirming bidirectional host traffic on all four LAN ports with this image.
The running LAN MAC matches the unit's NVRAM base address
`44:d4:37:78:c6:a0`; wan-cpu has the expected index-4 address ending in a4.
The same boot mounts rootfs_0 and restores the configured hostname.
Flash-image DHCP/routed traffic and the copper WAN's provisioned MAC
still need confirmation with this image.
The log still contains the PMC firmware reservation warning, CFE metadata
lookup warnings for images 86/87, and a deferred gpio-keys probe. These did
not prevent this boot; their causes remain separate follow-up work.

Host regression tests (including mocked flash updates and MAC provisioning):

```
python3 target/linux/bcmbca/tests/test_bcmbca.py \
    --cferam dl/genexis-xg6846b-cferam.000 -v
```

These tests use the staged host zlib, mtd-utils and y3-image. Their small
filesystem and kernel fixtures are format tests, not installation images.

The initial images have also been checked with the dump's actual CFE Y3
parser and LZMA decoder under QEMU, using stubs for hardware operations.
A separate ARM QEMU NAND simulator test exercised the generated UBI,
root filesystem growth, both sysupgrade modes, configuration restoration,
and preservation of the loader area and metadata. These checks do not
emulate the BCM6846 hardware or establish direct CFE-to-Linux boot.

The XRDP reset path powers on the BCM6846 XRDP, Runner and WAN zones
before accessing their register banks. This is required for direct CFE
boot, which can leave Runner registers inaccessible. The driver skips MMIO
cleanup if power-up or reset failed. To exercise the actual kernel reset
and initialization code with a PMB/MMIO model after `make target/linux/prepare`:

```
python3 target/linux/bcmbca/tests/test_xrdp_power.py --kernel /path/to/prepared/linux-6.18.x
```

The quad-PHY power sequence has a separate host model covering the CFE
register trace, port masks, preserved fields, delays, MDIO exclusion,
readback failures, deferred probe and device-tree reference cleanup:

```
python3 target/linux/bcmbca/tests/test_qegphy_power.py --kernel /path/to/prepared/linux-6.18.x
```
