from __future__ import annotations

from app.channel.fake import FakeChannel
from app.channel.port import ChannelPort, InboundMessage
from tests.channel_contract import ChannelContract


class TestFakeChannel(ChannelContract):
    def make_channel(self) -> ChannelPort:
        return FakeChannel()

    def stage_inbound(self, channel: ChannelPort, message: InboundMessage) -> None:
        assert isinstance(channel, FakeChannel)
        channel.stage_inbound(message)

    def stage_media(self, channel: ChannelPort, media_ref: str, data: bytes) -> None:
        assert isinstance(channel, FakeChannel)
        channel.stage_media(media_ref, data)

    def received(self, channel: ChannelPort) -> list[InboundMessage]:
        assert isinstance(channel, FakeChannel)
        return channel.inbound
