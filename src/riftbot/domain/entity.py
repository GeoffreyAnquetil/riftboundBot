from dataclasses import dataclass


@dataclass(kw_only=True)
class Entity:
    """Base des entités persistées. ``id`` vaut ``None`` tant que l'entité n'est pas enregistrée."""

    id: int | None = None

    @property
    def pk(self) -> int:
        """Identifiant garanti non nul, à utiliser une fois l'entité persistée."""
        if self.id is None:
            raise RuntimeError(f"{type(self).__name__} n'est pas encore persisté")
        return self.id
