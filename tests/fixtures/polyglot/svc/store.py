import enum


class Kind(enum.Enum):
    BOOK = 1
    FILM = 2


def load(item_id: int):
    return {"id": item_id, "kind": Kind.BOOK}
