# SPDX-License-Identifier: GPL-2.0-only

REQUIRE_IMAGE_METADATA=1
RAMFS_COPY_BIN='ubiupdatevol bcmbca-bootcheck'
CI_UBIPART=image
CI_ROOTPART=rootfs_0
CI_KERNPART=kernel_0

bcmbca_check_tar() {
	local image="$1" dir="sysupgrade-genexis_xg6846b" tmp

	# Only the OpenWrt sysupgrade tar is accepted, never a factory Y3.
	[ "$(tar tf "$image" 2>/dev/null | sort)" = "$(printf '%s\n' \
		"$dir/" "$dir/CONTROL" "$dir/kernel" "$dir/root" | sort)" ] || return 1
	tmp="$(mktemp -d /tmp/bcmbca-check.XXXXXX)" || return 1
	if ! tar xOf "$image" "$dir/CONTROL" > "$tmp/CONTROL" ||
	   ! tar xOf "$image" "$dir/kernel" > "$tmp/kernel" ||
	   ! tar xOf "$image" "$dir/root" > "$tmp/root" ||
	   [ "$(cat "$tmp/CONTROL")" != "BOARD=genexis_xg6846b" ] ||
	   [ "$(identify "$tmp/root" cat)" != ubifs ] ||
	   ! bcmbca-bootcheck "$tmp/kernel"; then
		rm -rf "$tmp"
		return 1
	fi
	rm -rf "$tmp"
}

bcmbca_check_layout() {
	local ubi="$1" id expected count=0 volume

	[ -n "$ubi" ] || return 1
	for volume in /sys/class/ubi/${ubi}_*; do
		[ -f "$volume/name" ] && count=$((count + 1))
	done
	[ "$count" = 4 ] || return 1
	for id in 0 1 2 10; do
		case "$id" in
		0) expected=rootfs_0 ;;
		1) expected=METADATA ;;
		2) expected=METADATACOPY ;;
		10) expected=kernel_0 ;;
		esac
		[ "$(cat /sys/class/ubi/${ubi}_$id/name 2>/dev/null)" = "$expected" ] || return 1
	done
	[ "$(cat /sys/class/ubi/${ubi}_0/type)" = dynamic ] &&
	[ "$(cat /sys/class/ubi/${ubi}_10/type)" = static ]
}

platform_check_image() {
	[ "$(board_name)" = "genexis,xg6846b" ] || return 1
	bcmbca_check_layout "$(nand_find_ubi image)" || {
		echo "Unsupported flash layout: install the OpenWrt factory image through CFE first."
		return 1
	}
	bcmbca_check_tar "$1" || {
		echo "Invalid XG6846B sysupgrade archive or CFE boot bundle."
		return 1
	}
}

bcmbca_upgrade() {
	local image="$1" ubi dir="sysupgrade-genexis_xg6846b"
	local tmp="/tmp/bcmbca-upgrade" kernel_size root_size

	ubi="$(nand_find_ubi image)"
	bcmbca_check_layout "$ubi" || return 1
	mkdir -p "$tmp" || return 1
	tar xOf "$image" "$dir/kernel" > "$tmp/kernel" || return 1
	tar xOf "$image" "$dir/root" > "$tmp/root" || return 1
	bcmbca-bootcheck "$tmp/kernel" || return 1
	[ "$(identify "$tmp/root" cat)" = ubifs ] || return 1
	kernel_size="$(wc -c < "$tmp/kernel")"
	root_size="$(wc -c < "$tmp/root")"
	[ "$kernel_size" -le "$(( $(cat /sys/class/ubi/${ubi}_10/reserved_ebs) * $(cat /sys/class/ubi/${ubi}_10/usable_eb_size) ))" ] || return 1
	[ "$root_size" -le "$(( $(cat /sys/class/ubi/${ubi}_0/reserved_ebs) * $(cat /sys/class/ubi/${ubi}_0/usable_eb_size) ))" ] || return 1
	ubi_mknod /sys/class/ubi/${ubi}_0
	ubi_mknod /sys/class/ubi/${ubi}_10

	# Keep the volume table, fixed IDs, CFE metadata and kernel reservation.
	# CFE RAM is embedded in the checked bundle and must never be omitted.
	ubiupdatevol "/dev/${ubi}_0" "$tmp/root" || return 1
	ubiupdatevol "/dev/${ubi}_10" "$tmp/kernel" || return 1
	rm -rf "$tmp"
	nand_do_restore_config || return 1
	sync
}

platform_do_upgrade() {
	if ! bcmbca_upgrade "$1"; then
		echo "Upgrade failed. Keep power on; opening a recovery shell on the serial console."
		# Never automatically reboot with an incomplete CFE RAM volume.
		# Normal console services have already been killed by sysupgrade.
		while :; do
			/bin/ash -i < /dev/console > /dev/console 2>&1
			sleep 1
		done
	fi
}
