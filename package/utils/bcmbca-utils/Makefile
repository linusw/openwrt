# SPDX-License-Identifier: GPL-2.0-only

include $(TOPDIR)/rules.mk

PKG_NAME:=bcmbca-utils
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-only

include $(INCLUDE_DIR)/package.mk

define Package/bcmbca-utils
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=BCMBCA CFE boot image and NVRAM utilities
  DEPENDS:=@TARGET_bcmbca +zlib
endef

define Build/Compile
	$(TARGET_CC) $(TARGET_CFLAGS) $(TARGET_CPPFLAGS) -Wall -Wextra \
		-o $(PKG_BUILD_DIR)/bcmbca-nvram $(PKG_BUILD_DIR)/bcmbca-nvram.c \
		$(TARGET_LDFLAGS) -lz
	$(TARGET_CC) $(TARGET_CFLAGS) $(TARGET_CPPFLAGS) -Wall -Wextra \
		-o $(PKG_BUILD_DIR)/bcmbca-bootcheck $(PKG_BUILD_DIR)/bcmbca-bootcheck.c \
		$(TARGET_LDFLAGS) -lz
endef

define Package/bcmbca-utils/install
	$(INSTALL_DIR) $(1)/usr/sbin
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/bcmbca-nvram $(1)/usr/sbin/
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/bcmbca-bootcheck $(1)/usr/sbin/
endef

$(eval $(call BuildPackage,bcmbca-utils))
