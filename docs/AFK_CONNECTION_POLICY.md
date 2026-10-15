# AFK connection policy

Authenticated players have no inactivity read deadline. The former hard-coded
180-second deadline has been removed. Unauthenticated peers still have 20 seconds
to log in; an incomplete frame still has 30 seconds to finish. Connection quotas,
frame-size limits, command rate limits, token expiry and session revocation remain.

The actual TCP handler is exercised by `server/test_afk_connections.py`: a silent
logged-in socket stays open beyond the scaled old idle and login deadlines, then
responds to another command. Incomplete frames and unauthenticated silence still
close. The normal Windows runtime passed 8 selected transport/session regressions;
the staged production source passed all 3 AFK-specific tests on the server runtime.

Live activation on 2026-10-13 replaced only the timeout delta in the deployed
`/opt/infinity/server/server.py`, preserving its other changes. The game service
restarted once (PID 579492 -> 601678); the API process was unchanged. Both services
are active; local and public TCP port 5588 connect successfully. Installed source
SHA256: `676fdfdfe8cc6acadfb1c194a34a127af5f93824d0ac53b6d856064aa8a0bc55`.

Backup: `/opt/infinity/backups/allow-afk-20261013T122923Z`.
Before source, exact staged source, deployment, tests and live verification are
recorded in `outputs/afk_idle_fix/`. Activation included atomic replacement and
rollback on failed startup. Later rollback must preserve subsequent source edits.
A real player's extended AFK session has not been observed; the inactivity policy
and socket behavior were verified as described above.
