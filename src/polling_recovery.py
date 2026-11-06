import logging
import time

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.methods import GetUpdates

from src.handlers.old_messages import OldMessagesMiddleware

logger = logging.getLogger(__name__)
MAX_POLL_PAUSE_SECONDS = 30


class PollingRecoveryMiddleware(BaseRequestMiddleware):
    """Advance the message cutoff after polling errors or a long process pause."""

    def __init__(self, message_gate: OldMessagesMiddleware) -> None:
        self.message_gate = message_gate
        self.last_response_at = time.time()
        self.interrupted = False

    async def __call__(self, make_request, bot, method):
        if not isinstance(method, GetUpdates):
            return await make_request(bot, method)
        started = time.time()
        paused = started - self.last_response_at > MAX_POLL_PAUSE_SECONDS
        try:
            result = await make_request(bot, method)
        except Exception:
            # aiogram retries polling itself; the next success must discard
            # commands accumulated while the connection was unavailable.
            self.interrupted = True
            raise
        finished = time.time()
        delayed = finished - started > (method.timeout or 0) + MAX_POLL_PAUSE_SECONDS
        if self.interrupted or paused or delayed:
            # Message timestamps have whole-second precision. Exclude the
            # entire recovery second so its queued commands cannot slip through.
            self.message_gate.discard_before(int(finished) + 1)
            logger.info("Polling resumed; queued photos will be collected, old messages ignored")
        self.last_response_at = finished
        self.message_gate.poll_received(finished)
        self.interrupted = False
        return result
