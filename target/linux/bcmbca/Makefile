# SPDX-License-Identifier: GPL-2.0-only

include $(TOPDIR)/rules.mk

ARCH:=arm
BOARD:=bcmbca
BOARDNAME:=Broadcom Broadband Carrier Access
FEATURES:=gpio nand ramdisk ubifs
CPU_TYPE:=cortex-a7
SUBTARGETS:=bcm6846

KERNEL_PATCHVER:=6.18
KERNELNAME:=zImage dtbs

include $(INCLUDE_DIR)/target.mk
include $(TOPDIR)/target/linux/bcmbca/kernel.mk

DEFAULT_PACKAGES += bcmbca-utils kmod-gpio-button-hotplug ubi-utils

$(eval $(call BuildTarget))
