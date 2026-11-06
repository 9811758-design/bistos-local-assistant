"""Directional aliases for explicitly identified catalog scopes."""

import re
import unicodedata
from collections.abc import Iterable
from typing import Final

from bistos.search import normalized

# These are catalog identities, not permission to search another product's documents.
# In particular, a human or veterinary suffix is never stripped automatically.
PRODUCT_MEMBERS: Final = {
    # The BT-410 family manual explicitly covers both head-worn light models;
    # selecting an unregistered leaf model must not gain the reverse alias.
    "BT-410": ("BT-410F", "BT-410A"),
    "BT-100-Accessory-Kit": ("BT-100",),
    "BT-100-EASY-LIFE": ("BT-100",),
    "BT-150-APP": ("BT-150",),
    "BT-200-Baby-Heart": ("BT-200",),
    "BT-200-LT": ("BT-200L", "BT-200T"),
    "BT-200-LTV": ("BT-200L", "BT-200T", "BT-200V"),
    "BT-200V-MDPRO": ("BT-200V",),
    "BT-350L-MDPRO": ("BT-350L",),
    "BT-400-Install": ("BT-400",),
    "BT-400N-Install": ("BT-400N",),
    "BT-550-Oxygen-Supply": ("BT-550",),
    "BT-700-Disposal": ("BT-700",),
    "BT-740-051": ("BT-740",),
    "BT-740A-051": ("BT-740A",),
    "BT-770-051": ("BT-770",),
    "BT-770A-051": ("BT-770A",),
    "Hi-Bebe-Super": ("BT-150L", "BT-150S"),
    "NeoNest": ("BT-550",),
    "Infantia-NB-500": ("BT-500",),
    "Motif-Phototherapy-Blanket": ("BT-450",),
    "BT-710V": ("BT-710 VET", "BT-710 Veterinary"),
    "BT-740V": ("BT-740 VET", "BT-740 Veterinary"),
    "BT-750V": ("BT-750 VET", "BT-750 Veterinary"),
    "BT-770V": ("BT-770 VET", "BT-770 Veterinary"),
    "BT-780V": ("BT-780 VET", "BT-780 Veterinary"),
}
MODEL_ALIASES: Final = {
    normalized(product): {normalized(product), *(normalized(member) for member in members)}
    for product, members in PRODUCT_MEMBERS.items()
}
MODEL_MENTION: Final = re.compile(
    r"""(?<![A-Za-z0-9])(?:(?<![가-힣])(?:동물용|수의용)[ \t]*|
    (?:VETERINARY|VET)[ \t]+)?
    BT[- ]?\d+[A-Za-z]*(?:-[A-Za-z0-9]+)*
    (?:[ \t]+(?:VETERINARY|VET|MDPRO|DISPOSAL|APP)|[- \t]*(?:동물용|수의용))?
    (?![A-Za-z0-9])""",
    re.IGNORECASE | re.VERBOSE,
)

# Exact identities from the registered catalog. These spelling aliases do not imply
# that an OEM product can use the base Bistos product's manual, or vice versa.
NAMED_PRODUCTS: Final = {
    "BCM350N": ("BCM 350N",),
    "NeoNest": ("Neo Nest",),
    "Infantia-NB-500": ("Infantia NB 500",),
    "HMG-331": (),
    "HMG-332": (),
    "HMG-333": (),
    "HMG-334": (),
    "PT-5000-NeoLight": ("PT-5000 NeoLight",),
    "NeoGenesis": ("Neo Genesis",),
    "FM-8000": (),
    "FM-3000-Plus": (),
    "Motif-Phototherapy-Blanket": (),
    "ENCORE-PLUS": (),
    "ENCORE": (),
    "AddMaxPlus": ("Add Max Plus",),
    "Motif-Duo-Refresh": (),
    "Flux200": ("Flux 200",),
    "SMEBP-100": (),
    "ECOsound": ("ECO sound",),
    "Hi-Bebe-Super": (),
    "Biocare-FM-1": (),
    "Ulmuak": ("Ulmuka",),
    "Patient-Monitors-WiFi": (),
    "Patient-Monitors-CMS": (),
    "F10": (),
    "FM20": ("FM 20",),
    "ECOtwin": ("ECO twin",),
    "Lynx15": ("Lynx 15",),
    "Lynx8": ("Lynx 8",),
}
NAMED_IDENTITIES: Final = {
    normalized(alias): normalized(product)
    for product, aliases in NAMED_PRODUCTS.items()
    for alias in (product, *aliases)
}
NAMED_MENTION: Final = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    + "|".join(
        r"[- \t_]*".join(re.escape(part) for part in re.split(r"[- _]+", alias))
        for alias in sorted(
            (alias for product, aliases in NAMED_PRODUCTS.items() for alias in (product, *aliases)),
            key=len,
            reverse=True,
        )
    )
    + r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)
VETERINARY_BT_PRODUCTS: Final = (
    "BT-710V", "BT-740V", "BT-750V", "BT-770V", "BT-780V",
)
# Scope is established from the manual, not its suffix: BT-200V is vascular,
# while Lynx OEM manuals are veterinary without a V suffix.
VETERINARY_SCOPES: Final = frozenset(
    normalized(product) for product in (*VETERINARY_BT_PRODUCTS, "Lynx15", "Lynx8")
)
VETERINARY_IDENTITIES: Final = {
    normalized(alias): normalized(product)
    for product in VETERINARY_BT_PRODUCTS
    for name in (product, product[:-1])
    for qualifier in ("VET", "Veterinary", "동물용", "수의용")
    for alias in (f"{name} {qualifier}", f"{qualifier} {name}")
}


def _shorthand_models(question: str, catalog: Iterable[str]) -> list[str]:
    bases = {match.group(1) for product in catalog
             if (match := re.match(r"BT[- ]?(\d+)", product, re.IGNORECASE))}
    mentions: list[str] = []
    units = (
        r"시간|분|초|일|년|개월|페이지|쪽|개|회|도|℃|°|%",
        r"(?:hours?|minutes?|seconds?|pages?|mmHg|mAh|Hz|kg|mm|cm|mA)\b|V(?![A-Za-z])",
    )
    for match in re.finditer(
        r"(?<![\w.:/+-])(\d{3,4})([A-Za-z]*(?:-[A-Za-z0-9]+)*)(?![A-Za-z0-9.:/-])",
        question,
    ):
        if match.group(1) not in bases:
            continue
        # A catalog number followed by a unit/page counter is a quantity.
        if re.match(
            r"\s*(?:" + "|".join(units) + ")",
            question[match.end():], re.IGNORECASE,
        ):
            continue
        if re.search(r"(?:전압|온도|시간|횟수|페이지|voltage|temperature)\s*[:=]?\s*$",
                     question[:match.start()], re.IGNORECASE):
            continue
        mention = "BT-" + match.group()
        qualifier = re.match(r"\s*(VETERINARY|VET|동물용|수의용)",
                             question[match.end():], re.IGNORECASE)
        if qualifier:
            mention += " " + qualifier.group(1)
        mentions.append(mention)
    return mentions


def model_mismatch(
    product_id: str, question: str, *, catalog: Iterable[str] | None = None,
) -> bool:
    question = unicodedata.normalize("NFKC", question).translate(
        str.maketrans(dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe63\uff0d", "-"))
    )
    selected = normalized(product_id)
    allowed = MODEL_ALIASES.get(selected, {selected})
    veterinary = re.search(r"동물용|수의용|\b(?:VET|VETERINARY)\b", question, re.IGNORECASE)
    if veterinary and allowed.isdisjoint(VETERINARY_SCOPES):
        return True
    if catalog is not None and any(
        VETERINARY_IDENTITIES.get(normalized(mention), normalized(mention)) not in allowed
        for mention in _shorthand_models(question, catalog)
    ):
        return True
    for match in MODEL_MENTION.finditer(question):
        mention = normalized(match.group())
        if VETERINARY_IDENTITIES.get(mention, mention) not in allowed:
            return True
    return any(
        NAMED_IDENTITIES[normalized(match.group())] != selected
        for match in NAMED_MENTION.finditer(question)
    )
