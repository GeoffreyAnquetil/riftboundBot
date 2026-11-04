import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

Handler = Callable[[Any], Awaitable[None]]


class LocalEventBus:
    """Bus d'événements en mémoire, dans le même processus.

    Les handlers sont appelés dans l'ordre d'abonnement, après le commit de l'émetteur. Par
    défaut, l'échec d'un handler est journalisé sans annuler l'action de l'émetteur.
    """

    def __init__(self, *, raise_errors: bool = False) -> None:
        self._handlers: defaultdict[type, list[Handler]] = defaultdict(list)
        self._raise_errors = raise_errors

    def subscribe(self, event_type: type[Any], handler: Handler) -> None:
        self._handlers[event_type].append(handler)

    async def publish(self, event: object) -> None:
        for handler in self._handlers[type(event)]:
            try:
                await handler(event)
            except Exception:
                if self._raise_errors:
                    raise
                logger.exception("Échec du handler %s pour %r", handler, event)
