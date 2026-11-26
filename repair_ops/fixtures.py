"""Fictional records and the shared shop authorization boundary."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Identity:
    shops: frozenset[str]
    reviewer: bool = False


IDENTITIES = {
    "cedar-writer": Identity(frozenset({"Cedar Arc"})),
    "flint-writer": Identity(frozenset({"Flint Vale"})),
    "regional-reviewer": Identity(frozenset({"Cedar Arc", "Flint Vale"}), True),
}


@dataclass(frozen=True)
class Order:
    id: str
    shop: str
    description: str
    attachments: tuple[str, ...] = ()


ORDERS = {
    order.id: order
    for order in (
        Order("RO-101", "Cedar Arc", "Front bumper camera calibration"),
        Order("RO-102", "Cedar Arc", "Camera calibration verification", ("calibration report",)),
        Order("RO-103", "Cedar Arc", "Wheel alignment verification"),
        Order("RO-104", "Cedar Arc", "Seat upholstery inspection"),
        Order("RO-201", "Flint Vale", "Camera calibration verification"),
    )
}


@dataclass(frozen=True)
class Passage:
    document_id: str
    version: int
    shop: str
    keywords: tuple[str, ...]
    required_attachment: str
    text: str
    current: bool = True

    @property
    def id(self) -> str:
        return f"{self.document_id}@{self.version}"


PASSAGES = (
    Passage("CED-CAMERA", 1, "Cedar Arc", ("camera", "calibration"), "checklist",
            "Archive the camera checklist in the repair file.", False),
    Passage("CED-CAMERA", 2, "Cedar Arc", ("camera", "calibration"), "calibration report",
            "Attach the calibration report to the repair file before closing it."),
    Passage("CED-ALIGN", 1, "Cedar Arc", ("wheel", "alignment"), "alignment record",
            "Archive the alignment record with the repair file.", False),
    Passage("FLT-CAMERA", 1, "Flint Vale", ("camera", "calibration"), "verification sheet",
            "File the camera verification sheet with the repair order."),
)


def lookup_order(identity: Identity, order_id: str) -> Order | None:
    order = ORDERS.get(order_id)
    return order if order and order.shop in identity.shops else None


def retrieve(identity: Identity, order: Order) -> Passage | None:
    """Rank exact keyword overlaps, then prefer the latest version on ties."""
    words = set(re.findall(r"[a-z]+", order.description.lower()))
    candidates = [
        passage for passage in PASSAGES
        if passage.shop in identity.shops and passage.shop == order.shop
        and words.intersection(passage.keywords)
    ]
    return max(
        candidates,
        key=lambda passage: (len(words.intersection(passage.keywords)), passage.version),
        default=None,
    )
