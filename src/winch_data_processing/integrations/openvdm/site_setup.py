"""Builds the per-ship site TOML for the OpenVDM hook by asking for each field (ported from
nav-qa-processing).

Prompts go to stderr, so the TOML alone can go to stdout. Each prompt shows its default in
brackets: Enter keeps it, ``-`` clears it. At the end of input every remaining field keeps its
default, so with no terminal an existing file is written back unchanged.

For each winch it asks what the hook needs to find and read its data and judge its tension
member: its transfer, data format and files, and the tension member's identifier, kind, NBL,
selected factor of safety and diameter. What's longer to type and changes less often (the
sheaves, the monitoring system, the break tests, units other than lbf and inches) is kept from
an existing file and otherwise edited in it, as `configs/site.example.toml` shows.
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TextIO

from winch_data_processing import formats, rvss
from winch_data_processing.integrations.openvdm.hook import SiteConfig, SiteWinch
from winch_data_processing.rvss import Kind
from winch_data_processing.wirelog import safe_name

CLEAR = "-"


@dataclass
class Prompter:
    stdin: TextIO = field(default_factory=lambda: sys.stdin)
    out: TextIO = field(default_factory=lambda: sys.stderr)
    ended: bool = False  # end of input: every later prompt keeps its default

    def say(self, text: str) -> None:
        self.out.write(text + "\n")

    def ask(
        self, label: str, default: str = "", check: Callable[[str], str | None] | None = None
    ) -> str:
        """The answer, or default; check returns a problem to re-ask with, or None"""
        while True:
            self.out.write(f"{label} [{default}]: " if default else f"{label}: ")
            self.out.flush()
            line = "" if self.ended else self.stdin.readline()
            if not line:
                self.ended = True
                self.out.write("\n")
                return default
            answer = line.strip()
            answer = "" if answer == CLEAR else answer or default
            problem = check(answer) if check else None
            if problem is None:
                return answer
            if self.ended:  # no one to re-ask; keep the default rather than loop
                return default
            self.say(f"  {problem}")


def _email(text: str) -> str | None:
    return None if not text or ("@" in text and " " not in text) else "not an email address"


def _required(text: str) -> str | None:
    return None if text else "required"


def _format(text: str) -> str | None:
    """None for a format ID with a reader; the hook can't read the winch without one"""
    if text in formats.READERS:
        return None
    problem = "required" if not text else f"no reader for {text}"
    return f"{problem}: one of {', '.join(formats.READERS)}"


def _kind(text: str) -> str | None:
    return (
        None if text in {k.value for k in Kind} else f"one of {', '.join(k.value for k in Kind)}"
    )


def _positive(required: bool = True) -> Callable[[str], str | None]:
    def check(text: str) -> str | None:
        if not text:
            return "required" if required else None
        try:
            return None if float(text) > 0 else "must be above 0"
        except ValueError:
            return "not a number"

    return check


def _factor_of_safety(kind: str) -> Callable[[str], str | None]:
    """None for an FS Appendix A allows for the kind (rvss.table_for)"""

    def check(text: str) -> str | None:
        problem = _positive()(text)
        if problem:
            return problem
        try:
            rvss.table_for(Kind(kind), float(text))
        except rvss.StandardError as error:
            return str(error)
        return None

    return check


def _number(text: str) -> int | float:
    value = float(text)
    return int(value) if value.is_integer() and "." not in text else value


def _text(value) -> str:
    return "" if value is None else str(value)


def _set(table: dict, key: str, value) -> None:
    """table[key] = value, or no key for a blank value"""
    if value in ("", None):
        table.pop(key, None)
    else:
        table[key] = value


def _ask_winch(prompt: Prompter, name: str, default: SiteWinch) -> SiteWinch:
    """A winch's transfer, data and tension member, starting from default; its other tables
    are default's
    """
    tables = {
        key: dict(value) if isinstance(value, dict) else value
        for key, value in default.tables.items()
    }
    winch, data = tables.setdefault("winch", {}), tables.setdefault("data", {})
    member = tables.setdefault("tension_member", {})
    winch["name"] = name
    transfer = prompt.ask(
        "  the collection system transfer it's in, e.g. OpenRVDAS (blank: any the hook runs for)",
        default.transfer,
    )
    for key, label in (
        ("manufacturer", "winch manufacturer (e.g. Markey)"),
        ("model", "winch model (e.g. DESH-5)"),
    ):
        _set(winch, key, prompt.ask(f"  {label}", _text(winch.get(key))))

    data["format"] = prompt.ask("  data format", _text(data.get("format")), _format)
    _set(
        data,
        "files",
        prompt.ask(
            "  glob of its files within the collection system's directory, e.g. *hydro*.txt "
            "(blank: every file)",
            _text(data.get("files")),
        ),
    )
    _set(
        winch,
        "record_name",
        prompt.ask(
            "  its name in the records, to keep only its own (LCI-90i Name; blank: every record)",
            _text(winch.get("record_name")),
        ),
    )
    if data["format"] == "delimited":
        columns = dict(data.get("columns", {}))
        prompt.say("  The delimited file's columns, by header name or 0-based index:")
        for key, required in (
            ("time", True),
            ("tension", True),
            ("payout", False),
            ("speed", False),
        ):
            answer = prompt.ask(
                f"    {key}", _text(columns.get(key)), _required if required else None
            )
            _set(columns, key, int(answer) if answer.isdigit() else answer)
        data["columns"] = columns

    prompt.say("  Its tension member (A.5.2.1, A.5.1.1):")
    member["id"] = prompt.ask(
        "    NSF tension member identifier", _text(member.get("id")), _required
    )
    member["kind"] = prompt.ask(
        f"    kind ({', '.join(k.value for k in Kind)})", _text(member.get("kind")), _kind
    )
    _set(member, "description", prompt.ask("    description", _text(member.get("description"))))
    tension_unit = member.get("tension_unit", "lbf")
    diameter_unit = member.get("diameter_unit", "in")
    member["nominal_breaking_load"] = _number(
        prompt.ask(
            f"    nominal breaking load [{tension_unit}]",
            _text(member.get("nominal_breaking_load")),
            _positive(),
        )
    )
    member["factor_of_safety"] = _number(
        prompt.ask(
            "    selected factor of safety (sets the RVSS table)",
            _text(member.get("factor_of_safety")),
            _factor_of_safety(member["kind"]),
        )
    )
    for key, label in (
        ("diameter", "diameter d"),
        ("largest_wire_diameter", "largest wire diameter d1"),
    ):
        answer = prompt.ask(
            f"    {label} [{diameter_unit}]", _text(member.get(key)), _positive(required=False)
        )
        _set(member, key, _number(answer) if answer else None)
    return SiteWinch(name, tables, transfer)


def _name_check(taken: set[str]) -> Callable[[str], str | None]:
    def check(text: str) -> str | None:
        if not text:
            return None
        if safe_name(text) != text:
            return "use letters, digits, _ and -"
        return f"{text} is taken" if text in taken else None

    return check


def _ask_winches(prompt: Prompter, defaults: SiteConfig) -> list[SiteWinch]:
    prompt.say(
        "\nThe winches, each assessed with its own wire log and checks, "
        "<CRUISE>_<name>_...: a name for each (e.g. hydro), blank to finish, - to drop one "
        "already here. Data formats with a reader:"
    )
    for format_id, reader in formats.READERS.items():
        prompt.say(f"  {format_id:18} {reader.description}")
    winches: list[SiteWinch] = []
    taken: set[str] = set()
    for default in [*defaults.winches, None]:
        while True:
            name = prompt.ask("winch name", default.name if default else "", _name_check(taken))
            if not name:
                break
            taken.add(name)
            winches.append(_ask_winch(prompt, name, default or SiteWinch(name)))
            if default is not None:  # an existing winch: one name; new ones: until blank
                break
    new = [w.name for w in winches if "sheave" not in w.tables]
    if new:
        prompt.say(
            f"\nAdd {', '.join(new)}'s sheaves ([[winch.sheave]]), and every winch's "
            "[winch.monitoring] and break tests, in the file: configs/site.example.toml shows "
            "them. The checks they feed are n/a until then."
        )
    return winches


def prompt_site_config(
    defaults: SiteConfig, prompt: Prompter | None = None, from_openvdm: dict | None = None
) -> SiteConfig:
    """A site config from answers to prompts, starting from defaults. Each vessel field's
    default is the site config's value, or else from_openvdm's (openvdm.yaml's vessel settings,
    by SiteConfig field): the site config's win when the hook runs, and openvdm.yaml fills
    those left blank
    """
    prompt = prompt or Prompter()
    from_openvdm = from_openvdm or {}
    prompt.say(f"Enter keeps the [default]; {CLEAR} clears it.\n")
    if from_openvdm:
        prompt.say("openvdm.yaml's vessel block has these, offered where the site TOML has none:")
        for name, value in from_openvdm.items():
            prompt.say(f"  {name} = {value}")
        prompt.say("")

    def ask(field_name: str, label: str, check=None) -> str:
        default = getattr(defaults, field_name) or from_openvdm.get(field_name, "")
        return prompt.ask(label, default, check)

    site = replace(
        defaults,
        vessel_name=ask("vessel_name", "Vessel name (e.g. Roger Revelle)"),
        contact_institution=ask(
            "contact_institution", "Contact institution for the wire logs (the vessel operator)"
        ),
        contact_email=ask("contact_email", "Contact email", _email),
        output_extra_directory=ask(
            "output_extra_directory", "OpenVDM extra directory for the outputs"
        ),
    )
    site.winches = _ask_winches(prompt, defaults)
    return site
