"""The Husqvarna Automower integration."""
import asyncio
import logging
import time
from datetime import timedelta

from aioautomower import GetAccessToken, GetMowerData, RefreshAccessToken, Return

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_API_KEY,
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_USERNAME,
)
from homeassistant.core import Config, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ACCESS_TOKEN_RAW,
    CONF_PROVIDER,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    CONF_TOKEN_TYPE,
    DOMAIN,
    PLATFORMS,
    STARTUP_MESSAGE,
)

SCAN_INTERVAL = timedelta(seconds=300)

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: Config):
    """Set up this integration using YAML is not supported."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up this integration using UI."""

    if hass.data.get(DOMAIN) is None:
        hass.data.setdefault(DOMAIN, {})
        _LOGGER.info(STARTUP_MESSAGE)

    username = entry.data.get(CONF_USERNAME)
    password = entry.data.get(CONF_PASSWORD)
    api_key = entry.data.get(CONF_API_KEY)
    ### Handling missing information in Config Entries, when last start was with 2021.3.4 or earlier
    try:
        token = entry.data.get(CONF_TOKEN)
        access_token = token["access_token"]
        provider = token["provider"]
        token_type = token["token_type"]
        refresh_token = token["refresh_token"]
        token_expires_at = token["expires_at"]
    except:
        access_token = None
        provider = None
        token_type = None
        refresh_token = None
        token_expires_at = 0

    coordinator = AuthenticationUpdateCoordinator(
        hass,
        entry,
        username=username,
        password=password,
        api_key=api_key,
        access_token=access_token,
        provider=provider,
        token_type=token_type,
        refresh_token=refresh_token,
        token_expires_at=token_expires_at,
    )

    await coordinator.async_refresh()
    if not coordinator.last_update_success:
        raise ConfigEntryNotReady
    hass.data[DOMAIN][entry.entry_id] = coordinator
    for platform in PLATFORMS:
        if entry.options.get(platform, True):
            coordinator.platforms.append(platform)
            hass.async_add_job(
                hass.config_entries.async_forward_entry_setup(entry, platform)
            )
    return True


class AuthenticationUpdateCoordinator(DataUpdateCoordinator):
    """Update Coordinator."""

    def __init__(
        self,
        hass,
        entry,
        username,
        password,
        api_key,
        access_token,
        token_expires_at,
        provider,
        token_type,
        refresh_token,
    ):
        """Initialize."""
        _LOGGER.info("Inizialising UpdateCoordiantor")
        self.hass = hass
        self.entry = entry
        self.platforms = []
        self.username = username
        self.password = password
        self.api_key = api_key
        self.access_token = access_token
        self.access_token_raw = None
        self.provider = provider
        self.token_type = token_type
        self.refresh_token = refresh_token
        self.token_expires_at = token_expires_at
        self.mower_api = None
        self.get_token = GetAccessToken(self.api_key, self.username, self.password)
        self.update_config_entry = hass.config_entries
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)

    async def _async_update_data(self):
        """Update data via library."""
        _LOGGER.debug("Updating mower data")
        if self.access_token is None or self.token_expires_at < time.time():
            await self.async_update_token()

        self.mower_api = GetMowerData(
            self.api_key, self.access_token, self.provider, self.token_type
        )
        try:
            data = await self.mower_api.async_mower_state()
            return data
        except Exception as exception:
            raise UpdateFailed(exception)

    async def async_update_token(self):
        """Update token via library."""
        _LOGGER.debug("Updating token")
        if self.access_token is None:  ##deprecated, remove in 2021.6
            _LOGGER.debug("Getting new token, because is None")
            try:
                self.access_token_raw = await self.get_token.async_get_access_token()
                _LOGGER.debug("Token expires at %i UTC", self.token_expires_at)
            except Exception:
                _LOGGER.debug(
                    "Error message for UpdateFailed: %i",
                    self.access_token_raw["status"],
                )
                raise UpdateFailed("Error communicating with API")

        elif self.token_expires_at < time.time():
            _LOGGER.debug("Getting new token, because expired")
            self.refresh_token = RefreshAccessToken(self.api_key, self.refresh_token)
            try:
                self.access_token_raw = (
                    await self.refresh_token.async_refresh_access_token()
                )
                _LOGGER.debug("Token expires at %i UTC", self.token_expires_at)
            except Exception:
                _LOGGER.debug(
                    "Error message for UpdateFailed: %i",
                    self.access_token_raw["status"],
                )
                raise UpdateFailed("Error communicating with API")

        self.access_token = self.access_token_raw["access_token"]
        self.provider = self.access_token_raw["provider"]
        self.token_type = self.access_token_raw["token_type"]
        self.refresh_token = self.access_token_raw["refresh_token"]
        self.token_expires_at = self.access_token_raw["expires_at"]

        self.update_config_entry.async_update_entry(
            self.entry,
            data={
                CONF_PASSWORD: self.password,  ##deprecated, remove in 2021.6
                CONF_USERNAME: self.username,  ##deprecated, remove in 2021.6
                CONF_TOKEN: self.access_token_raw,
                CONF_API_KEY: self.api_key,
            },
        )

    async def async_send_command(self, payload, mower_id):
        """Send command to the mower."""
        self.mower_id = mower_id
        self.payload = payload
        self.mower_command = Return(
            self.api_key,
            self.access_token,
            self.provider,
            self.token_type,
            self.mower_id,
            self.payload,
        )
        try:
            await self.mower_command.async_mower_command()
            await self.async_request_refresh()
        except Exception as exception:
            raise UpdateFailed(exception)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Handle removal of an entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    unloaded = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, platform)
                for platform in PLATFORMS
                if platform in coordinator.platforms
            ]
        )
    )
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
