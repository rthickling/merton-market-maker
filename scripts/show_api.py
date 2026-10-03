#!/usr/bin/env python3
"""Print the reflected Python interface of the calibrator module.

Runs offline: it only imports the built module. Intended to be started from the
repository root, e.g. `cd cpp && just api`.
"""

from __future__ import annotations

import merton_online_calibrator as moc

TYPES = (moc.MertonParams, moc.CalibratorConfig, moc.OnlineMertonCalibrator)


def describe(cls) -> list[str]:
    lines = [f"{cls.__name__}:"]
    for name in sorted(n for n in dir(cls) if not n.startswith("_")):
        member = getattr(cls, name)
        is_property = isinstance(member, property)
        doc = getattr(member.fget if is_property else member, "__doc__", None)
        first = doc.splitlines()[0] if doc else ""
        if is_property:
            # Getter signatures are unnamed, e.g. "(self) -> float".
            _, arrow, type_name = first.rpartition("->")
            lines.append(f"    {name}: {type_name.strip()}" if arrow else f"    {name}")
        else:
            lines.append(f"    {first or name}")
    return lines


def main() -> None:
    print(f"module: {moc.__doc__}")
    for cls in TYPES:
        print()
        print("\n".join(describe(cls)))


if __name__ == "__main__":
    main()
