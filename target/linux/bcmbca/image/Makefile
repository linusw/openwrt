# SPDX-License-Identifier: GPL-2.0-only

include $(TOPDIR)/rules.mk
include $(INCLUDE_DIR)/image.mk

# Supply the original board's CFE RAM stage; it is also needed on sysupgrade.
BCMBCA_CFERAM ?= $(DL_DIR)/genexis-xg6846b-cferam.000

include $(TOPDIR)/target/linux/bcmbca/kernel.mk

define Build/bcm6846-boot-bundle
	$(STAGING_DIR_HOST)/bin/python3 $(CURDIR)/bcm6846-image.py boot \
		--kernel $@ --dtb $(KDIR)/image-$(DEVICE_DTS).dtb \
		--cferam $(BCMBCA_CFERAM) --output $@.new
	mv $@.new $@
endef

define Build/bcm6846-factory
	$(STAGING_DIR_HOST)/bin/python3 $(CURDIR)/bcm6846-image.py ubi \
		--kernel $(IMAGE_KERNEL) --rootfs $(IMAGE_ROOTFS) \
		--ubinize $(STAGING_DIR_HOST)/bin/ubinize --output $@.ubi
	$(STAGING_DIR_HOST)/bin/y3-image -i $@.ubi -o $@ \
		-c 6846 -m PANTHER -a 'all arm_xscale' \
		-r 'OpenWrt-$(VERSION_NUMBER)' -u GNX -g '$(REVISION)'
	rm $@.ubi
endef

define Device/Default
  DEVICE_DTS_DIR := $(DTS_DIR)/broadcom
  KERNEL_NAME := zImage
  KERNEL := kernel-bin | append-dtb | bcm6846-boot-bundle
  KERNEL_INITRAMFS := kernel-bin | append-dtb
  KERNEL_INITRAMFS_SUFFIX := -zImage
  KERNEL_DEPENDS := $(BCMBCA_CFERAM) $(CURDIR)/bcm6846-image.py
  FILESYSTEMS := ubifs
  # 2012 PEBs less the kernel reservation and UBI/metadata overhead.
  # This is an upper bound; mkfs.ubifs produces a compact, growing filesystem.
  MKUBIFS_OPTS := -m 2048 -e 126976 -c 1880
  IMAGES := factory.y3 sysupgrade.bin
  IMAGE/factory.y3 := bcm6846-factory
  IMAGE/sysupgrade.bin := sysupgrade-tar | append-metadata
endef

define Device/genexis_xg6846b
  DEVICE_VENDOR := Genexis
  DEVICE_MODEL := XG6846B
  DEVICE_DTS := bcm6846-genexis-xg6846b
endef
TARGET_DEVICES += genexis_xg6846b

$(eval $(call BuildImage))
