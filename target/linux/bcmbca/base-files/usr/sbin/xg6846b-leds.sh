#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Configure the XG6846B LEDs using the existing netdev/default-on triggers.
# Run automatically by /etc/init.d/xg6846b-leds after network setup.
# To reapply the board defaults manually:
#     /etc/init.d/xg6846b-leds restart
# Requires CONFIG_LEDS_TRIGGER_NETDEV and CONFIG_LEDS_TRIGGER_DEFAULT_ON
# (or load ledtrig-netdev and ledtrig-default-on before running this script).
# This is a one-shot setup; the kernel then follows link/speed and traffic.
# OpenWrt preinit renames the copper WAN port to eth4 and its fixed
# CPU-to-switch link to wan-cpu. The EXT LEDs follow the copper port.
# The green WAN LED follows the SFP port's link and activity at any speed.
# XG6846_SYSFS_ROOT overrides /sys for testing against a mock filesystem.

set -eu

sysfs=${XG6846_SYSFS_ROOT:-/sys}
leds=$sysfs/class/leds

fail()
{
	printf 'xg6846b-leds: %s\n' "$*" >&2
	exit 1
}

has_trigger()
{
	case " $(tr -d '[]' < "$1/trigger") " in
	*" $2 "*) return 0 ;;
	*) return 1 ;;
	esac
}

# Existing DT color/function pairs name the LED class devices. Keep the
# hardware LED index here to make the board mapping easy to check.
mapping='0 green:ext eth4 link_1000
1 amber:ext eth4 link_100
4 green:wan sfp link
8 green:lan1 eth0 link_1000
7 amber:lan1 eth0 link_100
10 green:lan2 eth1 link_1000
9 amber:lan2 eth1 link_100
12 green:lan3 eth2 link_1000
11 amber:lan3 eth2 link_100
14 green:lan4 eth3 link_1000
13 amber:lan4 eth3 link_100'

# Check every device and trigger before changing any LED.
while read -r index name iface mode; do
	[ -d "$sysfs/class/net/$iface" ] || fail "missing interface $iface"
	[ -w "$leds/$name/trigger" ] ||
		fail "LED$index ($name) is missing or not writable; run as root"
	has_trigger "$leds/$name" netdev ||
		fail "netdev trigger unavailable for $name; load ledtrig-netdev"
done <<EOF
$mapping
EOF

[ -w "$leds/red:power/trigger" ] ||
	fail 'LED6 (red:power) is missing or not writable; run as root'
has_trigger "$leds/red:power" default-on ||
	fail 'default-on trigger unavailable; load ledtrig-default-on'

printf '%s\n' default-on > "$leds/red:power/trigger"

while read -r index name iface mode; do
	led=$leds/$name
	# Recreate the trigger to clear earlier settings, including activity.
	printf '%s\n' none > "$led/trigger"
	cat "$led/max_brightness" > "$led/brightness"
	printf '%s\n' netdev > "$led/trigger"
	printf '%s\n' "$iface" > "$led/device_name"

	# Clear all exposed modes so rerunning also overrides existing policy.
	for path in "$led/link" "$led"/link_* "$led/half_duplex" \
		"$led/full_duplex" "$led/tx" "$led/rx" "$led/tx_err" "$led/rx_err"; do
		[ -f "$path" ] || continue
		printf '0\n' > "$path"
	done

	# The trigger hides speeds unsupported by the selected interface.
	# Leave a speed indicator off if the interface cannot support it.
	if [ ! -f "$led/$mode" ]; then
		printf 'xg6846b-leds: LED%s (%s): %s has no %s; left off\n' \
			"$index" "$name" "$iface" "$mode" >&2
		continue
	fi
	printf '1\n' > "$led/$mode"
	# The BCMBCA netdev trigger patch gates activity by the selected speed,
	# keeping the other color off while packets are transmitted or received.
	printf '1\n' > "$led/tx"
	printf '1\n' > "$led/rx"
	printf 'LED%s (%s): %s %s with RX/TX activity\n' \
		"$index" "$name" "$iface" "$mode"
done <<EOF
$mapping
EOF

printf 'LED6 (red:power): always on\n'
