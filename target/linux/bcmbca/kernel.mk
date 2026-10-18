# SPDX-License-Identifier: GPL-2.0-only

# RAM bring-up must not automatically attach or alter the vendor UBI.
define Kernel/Configure/Initramfs
	$(call Kernel/SetInitramfs,$(1),$(2)); \
	$(SED) 's/^CONFIG_CMDLINE=.*/CONFIG_CMDLINE="console=ttyS0,115200"/' $(2)/.config
endef
