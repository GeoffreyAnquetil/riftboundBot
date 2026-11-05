"""Erreurs métier.

Le message d'une ``DomainError`` est destiné à l'utilisateur final : la couche Discord
l'affiche tel quel. Les autres exceptions sont des bugs et sont seulement journalisées.
"""


class DomainError(Exception):
    """Base de toutes les erreurs métier."""


class NotFound(DomainError):
    pass


class NotAllowed(DomainError):
    pass


class AlreadyRegistered(DomainError):
    pass


class NotRegistered(DomainError):
    def __init__(
        self, message: str = "Tu ne fais pas partie du ladder : utilise /register."
    ) -> None:
        super().__init__(message)


class InvalidScore(DomainError):
    pass


class InvalidMatchState(DomainError):
    pass


class StaleReport(DomainError):
    """Le résultat affiché dans un message a changé depuis sa création.

    Utilisé par les boutons persistants : sans cette vérification, un clic sur
    un ancien message pourrait confirmer un score que l'adversaire n'a jamais vu.
    """


class WeekAlreadyOpen(DomainError):
    pass


class NoOpenWeek(DomainError):
    def __init__(self, message: str = "Aucune semaine n'est en cours.") -> None:
        super().__init__(message)


class NotEnoughPlayers(DomainError):
    pass


class UnresolvedDisputes(DomainError):
    pass


class BettingClosed(DomainError):
    pass


class AlreadyBet(DomainError):
    pass


class InvalidAmount(DomainError):
    pass


class InsufficientFunds(DomainError):
    pass


class ConcurrentModification(DomainError):
    def __init__(
        self, message: str = "Quelqu'un a modifié ces données en même temps : réessaie."
    ) -> None:
        super().__init__(message)
