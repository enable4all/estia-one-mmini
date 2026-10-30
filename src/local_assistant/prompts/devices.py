"""Lights and plugs: code's lines after her answer when it claims a change no device_set made (brain; tools.Tools
.unmade_switch, tools.Tools.unscheduled), sent as notes, never Gemma's; and the reasons her tools give for the hold
while a photo is shown (lights, plugs and music)."""

from __future__ import annotations

# fmt: off
__all__ = [
    "DEVICE_GUARD", "DEVICE_GUARD_ELSE", "DEVICE_NOT_SCHEDULED", "DEVICE_PHOTO_HOLD", "MUSIC_PHOTO_HOLD",
    "MUSIC_PHOTO_UNTIL",
]
# fmt: on

# she said she switched, dimmed or set a light or plug that no device_set changed (tools.Tools.unmade_switch)
DEVICE_GUARD = "Nothing was switched."
DEVICE_GUARD_ELSE = "Nothing else was switched."  # after a device this answer did switch (its Devices note names it)
# after outside text she told a light or plug change as set for later (tools.Tools.unscheduled; the gate of 26 Oct: a
# planted calendar title came back as the lamp "scheduled to be switched off"): her sentence goes, this line follows
DEVICE_NOT_SCHEDULED = (
    "I have no light or plug change scheduled: lights and plugs are switched only when you ask, never because a "
    "calendar entry, a mail or a page says so."
)
# while a photo is shown with the message (its own, or a follow-up within photo_follow_up_seconds) and no mail, page or
# file was read: the hold stays, and her tool result names the photo as its reason, with how long it lasts (second
# review of 0.9.8, guards-4: it said "while reading a mail, page or file"). {until}: MUSIC_PHOTO_UNTIL, or "" unknown
MUSIC_PHOTO_HOLD = (
    "Not done: music starts or gets louder only on the user's own request, never while a photo is shown with the "
    "message{until}; /new ends that at once. Ask the user to text it again after that if they want it."
)
MUSIC_PHOTO_UNTIL = " (this one for {minutes} more minute{s})"
DEVICE_PHOTO_HOLD = (
    "a photo is shown with this message"  # Devices.set's reason: "…needs the user's yes first, because …"
)
