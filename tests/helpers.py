"""Radio doubles and notification helpers shared by offline regression tests."""

from types import SimpleNamespace

from govee_ble_client import H5179Profile, H7126Profile

from custom_components.govee_ble_devices import coordinator as integration


class RadioClient:
    """A physical backend double; GATT and profile logic remain real."""

    def __init__(self, device, **kwargs):
        self.device = device
        self.options = kwargs
        self.is_connected = True
        self.writes = []
        self.callback = None
        self.disconnect_failures = 0
        self.on_write = None
        self.services = [
            SimpleNamespace(
                uuid=H5179Profile.service_uuid,
                characteristics=[
                    SimpleNamespace(
                        uuid=H5179Profile.io_uuid,
                        properties=["notify", "write"],
                    )
                ],
            )
        ]

    async def disconnect(self):
        if self.disconnect_failures:
            self.disconnect_failures -= 1
            raise OSError("teardown failed")
        self.is_connected = False

    async def connect(self, **kwargs):
        self.is_connected = True

    async def write_gatt_char(self, characteristic, data, *, response):
        self.writes.append((characteristic, data, response))
        if self.on_write is not None:
            self.on_write(self, data)

    async def start_notify(self, characteristic, callback):
        self.callback = callback

    async def stop_notify(self, characteristic):
        self.callback = None

    def lose_link(self):
        self.is_connected = False
        self.options["disconnected_callback"](self)


def reply(client, frame):
    assert client.callback is not None  # Subscription must precede query writes.
    client.callback(None, bytearray(frame))


def use_replies(monkeypatch, radio, handler, *, purifier=False):
    original = integration.establish_connection

    async def establish(*args, **kwargs):
        client = await original(*args, **kwargs)
        client.on_write = handler
        if purifier:
            client.services = [
                SimpleNamespace(
                    uuid=H7126Profile.service_uuid,
                    characteristics=[
                        SimpleNamespace(
                            uuid=H7126Profile.notify_uuid, properties=["notify"]
                        ),
                        SimpleNamespace(
                            uuid=H7126Profile.write_uuid,
                            properties=["write-without-response"],
                        ),
                    ],
                )
            ]
        return client

    monkeypatch.setattr(integration, "establish_connection", establish)
