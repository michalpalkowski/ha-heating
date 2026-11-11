"""Node roles and physical floors are independent, explicitly configured values."""

from enum import StrEnum


class Floor(StrEnum):
    GROUND = "ground"
    UPPER = "upper"

    def node_for_zone(self, index: int) -> int:
        if type(index) is not int or not 0 <= index < 9:
            raise ValueError("invalid protocol zone")
        physical = Floor.GROUND if index < 4 else Floor.UPPER
        return 1 if physical == self else 2
