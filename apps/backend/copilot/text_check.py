"""Checks on the model's own text: every number and code it mentions must come from the evidence it was given.

This catches invented numbers and codes, not a real number put in the wrong place.
"""

import re

_DASHES = re.compile(r"[\u2010-\u2015\u2212]")  # hyphens, non-breaking hyphen, en and em dashes, minus sign
_CODE = re.compile(r"\b[A-Z][A-Z0-9]{1,5}(?:-[A-Z0-9]+)+\b")  # PMP-VIB-HI, AST-BFP-101, ALM-000411, SOP-BFP-001
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")  # 24, 7.1, 11.07; not the 2 in a citation key such as T2
_ANY_NUMBER = re.compile(r"\d+(?:\.\d+)?")  # in the evidence: also the 18 in a timestamp such as T18:36


def unsupported(text: str, evidence: str) -> str | None:
    """The first code or number in `text` that is not in `evidence` ("code PMP-XYZ-HI", "number 31"), or None.

    Remove citation keys from `text` first. Codes are compared ignoring case and dash variants. A number may be
    rounded: 11.1 is supported by 11.07.
    """
    text, evidence = _DASHES.sub("-", text), _DASHES.sub("-", evidence)
    known = evidence.lower()
    for code in _CODE.findall(text):
        if code.lower() not in known:
            return f"code {code}"
    values = [float(n) for n in _ANY_NUMBER.findall(evidence)]
    for raw in _NUMBER.findall(_CODE.sub(" ", text)):
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        if not any(round(v, decimals) == float(raw) for v in values):
            return f"number {raw}"
    return None
