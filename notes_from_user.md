# Notes for Copilot Sessions

* SSH target is `ssh user@pizero.tailb42140.ts.net`. Permission to enter password 4142 via stdin; after login the repo lives at `~/meshapp` on the Pi (same layout as local).
* App run command is `uv run python main.py` from `~/meshapp`.
* Instructions come via `notes_from_user.md`; I will re-check it periodically and again before wrapping a response.
* After finishing all tasks, wait ~10-second before finishing; Check for further tasks or notes in `notes_from_user.md` and do them as well before exiting.
* When exiting, suggest a commit message for the changes you made, 10 - 20 words max. 

# Task

Please make the following changes to the project:

Checkout the repo.
Currently there are displayed a lot of different channels. most contain "unknown" messages. First question, where do the come from? 

Default channel is displayed as "Channel ?" Why? Also it is completly spammed by "TELEMETRY_APP", "NODEINFO_APP" and "POSITION_APP". Please change that to only save and display actual messages. For each node only keep track how many non-messages there were per type 1. in total and 2. during the last 24 hours (reset counter at 00:00).
As before use them for the "last seen" update, but don't display them anymore.

The refresh of the page is currently annoying, because it causes the page to jump back to the top. Please change that to only update the data without refreshing the whole page.

# Notes during the session

Here I will put notes, to give you directions during an ongoing query, periodically check this file for updates:
* Display the non-message counts on hover-over on the nodes-page