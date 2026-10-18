# BCM6846 / Genexis XG6846B support

This target uses the BCM6846 CFE SMP, PMC/PMB and reduced XRDP Ethernet
series from `linux-integrator`, branch `b4/genexis-xg6846b-ethernet`, through
commit `944f9ca9ebcc`, based on v7.4-rc1 and rebased onto Linux 6.18.55.
This includes the shared MFD PMB regmap transport, power-domain, reset and
clock providers, PVTMON temperature/voltage monitoring, watchdog restart
support, and the Broadcom internal PHY driver. The BCM6846 I2C node is also
backported. The Ethernet driver uses IRQ-driven NAPI receive, BQL and SRAM
copies, with timer polling available. Runner core control is separate from
the Ethernet driver, and its reduced program is assembled from symbolic source. This is
initial support without the vendor packet acceleration. The SFP network port
is enabled using the source series' Marvell 88E6320 PCS support. The 6.18
stack also backports the seven upstream scratch-register and 88E6321 SerDes
prerequisites that this support reuses. The source separates the SoC XRDP description
from the board Ethernet setup and includes the GPIO 40 SFP enable
in the switch/SFP patch.
The driver handles XRDP power-up, PHY block power and PHY teardown locking.
The loader protection, flash layout and factory MAC cells come from
the kernel board patch. The console-preserving UART shutdown patch and
link-speed-qualified LED activity remain OpenWrt-specific.
The MFD, reset and hwmon drivers explicitly include `mod_devicetable.h`
for their OF match tables when built against the 6.18 headers.

The source branch includes indexed `mac-base` NVMEM support. OpenWrt
already supplies the same implementation in generic patches 804 and
809-01, so the target stack does not duplicate it. The kernel defconfig
NVMEM addition is represented by this target's `config-6.18`.

The CFE SMP prerequisites use the explicit `brcm,bca-smp` CPU enable
method and include its binding. CPU1's `cpu-release-addr` still points to
the CFE mailbox at `0xff800568`. The board SMP patch also preserves the
holding pen by removing `no-map` from its reservation. Both CPUs have
`cpu-release-addr`; CPU0 uses zero because it is already running.

The PMB clock driver uses distinct RDP and BIU PLL register layouts and
checks their hardware lock indication without interpreting firmware-owned
power/reset controls. Zero dividers report an unknown rate.

XRDP acquires its PMB power domain through runtime PM before resetting the
engine or preparing its clocks. It releases the clocks and runtime PM
reference after stopping the datapath. The peripheral and memory-controller
domains remain always on; peripheral devices reference the shared domain
in the SoC device tree. PVTMON exposes temperature and seven
voltage inputs through hwmon, with temperature also available to the DT
thermal zone. On hardware, inspect these with:

```
cat /sys/class/hwmon/hwmon*/name
cat /sys/class/hwmon/hwmon*/temp1_input
cat /sys/class/hwmon/hwmon*/in*_input
cat /sys/class/thermal/thermal_zone*/temp
```

Values use millidegrees Celsius and millivolts.

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
misc-partition selector blank, and use **Update Software**. Use this web
interface to install Y3 images.

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

The kernel reads the factory base MAC through an MTD NVMEM fixed-layout
cell in the read-only 4 MiB `loader` partition. Its offset is `0x106a0`:
the primary CFE NVRAM starts at `0x10580`, and `ucaBaseMacAddr` is at byte
288 (`0x120`) within that structure, as defined by the vendor `bcm_hwdefs.h`.

The `mac-base` cell supplies indices 0..4 to the five XRDP ports. LAN
ports eth0..eth3 therefore have distinct factory MACs (base through base+3).
Copper WAN and SFP inherit base+4 from their DSA conduit, `wan-cpu`.
The bridges select a member's MAC without userspace address overrides.
The standard `mac-base` increment operates on all 48 bits.

Fixed-layout reads the primary six-byte address with NAND ECC and the
Ethernet helper validates it. It does not validate the entire NVRAM CRC,
check the pool count, or fall back to the `nVrAmDaT` mirror. An unreadable
or invalid declared NVMEM MAC prevents XRDP registration instead of
silently substituting a random address. Probe is deferred until NVMEM is
available. The UBI `image` partition still spans `0x00400000..0x0ff80000`;
the last four eraseblocks remain reserved for NAND bad-block tables.

Sysupgrade preserves existing UCI configuration, including old explicit
`macaddr` settings. Remove the previously generated MAC overrides from
`/etc/config/network` to use the kernel-assigned addresses; retain any
intentional custom MAC overrides. New configurations need no MAC setup.

## SFP module detection

GPIO 40 (bank gpio1, offset 8) is reserved as an output-high GPIO hog named
`sfp-enable`. GenXOS drives this pin high during normal operation and low
for optical blackout. The pin must be driven high for SFP EEPROM access.
The exact power-control circuitry has not been established, so this is
described as an enable line rather than a regulator supply.

The GPIO hog applies the enable state when the GPIO controller registers,
before the SFP driver probes the module. The hardware I2C controller keeps
its existing `brcm,brcmper-i2c` configuration. The Marvell switch port is
enabled with 1000BASE-X and in-band status. No TX-disable GPIO is described;
the SFP driver warns that modules will always be emitting.

## LEDs

The target explicitly enables the `netdev` and `default-on` LED triggers.
The board script `/usr/sbin/xg6846b-leds.sh` is adapted from the XG6846B
documentation stash and runs with `/bin/sh`. Its `xg6846b-leds` init service runs automatically
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
