"""Public integration identifiers; no installation-specific configuration."""

from homeassistant.const import Platform

DOMAIN = "ufh_heating"
PLATFORMS = [Platform.CLIMATE, Platform.BINARY_SENSOR, Platform.SENSOR, Platform.UPDATE]
VERSION = "0.2.4"
