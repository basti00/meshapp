# Notes for this project

* App run command is `uv run main.py` from `~/meshapp` (won't work locally due to missing serial device)
* A copy of the prod-database is at `meshapp.db` for reference. It may be out of date.
* In files dump_nodes and dump_messages some diagnostic print statements are available to help understand the data. 
* When exiting, suggest a commit message for the changes you made, 10 - 20 words max. 

* The prod pi lives at `ssh user@pizero.tailb42140.ts.net`. Permission to enter password 4142 via stdin; after login the repo lives at `~/meshapp` on the Pi (same layout as local).