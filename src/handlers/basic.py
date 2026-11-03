from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

HELP_TEXT = (
    "список команд:\n\n"
    "/mute [@username] [минуты] — мут, без указания времени - на минуту.\n"
    "/unmute [@username] — снять мут.\n"
    "вместо @username поддерживается мут ответом на сообщение пользователя.\n\n"
    "/all — тегнуть всех участников беседы.\n"
)


def create_router() -> Router:
    router = Router(name="basic")

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        await message.answer(
            "низае, меня зовут бумбон.\n\n"
            "список доступных команд — /help.\n"
        )

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        await message.answer(HELP_TEXT)

    @router.message(F.text, F.text.func(lambda text: "".join(text.split()).lower() in {"heheheha", "хихихиха"}))
    async def easter_egg(message: Message) -> None:
        await message.answer_sticker(
            "CAACAgEAAxkBAAED0Rxl4HXz1istdjIwjN3V1WGqBoTFHQACAQAE1VcW3gieTqO_9z80BA"
        )

    return router
