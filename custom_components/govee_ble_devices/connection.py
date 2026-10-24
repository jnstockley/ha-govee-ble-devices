"""Select explicit ATT write modes for the supported device profiles."""

from bleak.backends.characteristic import BleakGATTCharacteristic
from govee_ble_client import GattConnection, H5179Profile, H7126Profile


class ProfileGattConnection(GattConnection):
    """Keep the public GATT lifecycle and avoid Bleak's implicit write choice."""

    async def write(
        self,
        characteristic: str | BleakGATTCharacteristic,
        data: bytes,
        *,
        response: bool | None = None,
    ) -> None:
        uuid = (
            characteristic if isinstance(characteristic, str) else characteristic.uuid
        ).casefold()
        if response is None:
            if uuid == H7126Profile.write_uuid:
                # Captured H7126 transport uses write-without-response. H7102's
                # encrypted writer already supplies False explicitly upstream.
                response = False
            elif uuid == H5179Profile.io_uuid:
                # Preserve Bleak's current preference for this sensor while
                # resolving capabilities from this session's validated table.
                for service in self.services:
                    if service.uuid.casefold() != H5179Profile.service_uuid:
                        continue
                    for item in service.characteristics:
                        if item.uuid.casefold() == uuid:
                            response = "write" in item.properties
                            break
        await super().write(characteristic, data, response=response)
