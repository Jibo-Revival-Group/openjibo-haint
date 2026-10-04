"""Resolve device-picker selections and legacy robot names."""

from .const import DOMAIN


def resolve_targets(robots, selection, registry):
    if selection is None or selection == []:
        return list(robots.values())
    selections = [selection] if isinstance(selection, str) else selection
    matched = set()
    for selected in selections:
        device = registry.async_get(selected)
        if device is not None:
            entries = {identifier[1] for identifier in device.identifiers if identifier[0] == DOMAIN}
        else:
            entries = {entry_id for entry_id, data in robots.items() if data["name"] == selected}
        entries.intersection_update(robots)
        if not entries:
            raise ValueError("A selected Jibo robot is no longer configured")
        matched.update(entries)
    return [data for entry_id, data in robots.items() if entry_id in matched]
