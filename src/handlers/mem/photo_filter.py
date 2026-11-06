from aiogram.types import Message


def is_channel_forward(message: Message) -> bool:
    origin = message.forward_origin
    if origin is None:
        return False
    return origin.type == 'channel' or (
        origin.type == 'chat' and origin.sender_chat.type == 'channel'
    )


def export_is_channel_forward(message: dict) -> bool:
    return str(message.get('forwarded_from_id') or '').startswith('channel')
