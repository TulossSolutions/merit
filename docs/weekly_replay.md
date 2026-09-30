# Weekly archive replay

Approved scope: reconstruct real weekly rankings for all 12 archive award periods,
2015/16–2026/27, after fixture retrieval completes. Use existing formula v1.5,
verified profile/review positions, retained match statistics and trophy evidence.
No API calls, guessed movements, trophy overrides or replacement of public snapshots.

The retrieval gate requires a successful archive run, all 12 periods with zero
pending imports, six club editions per period, and no completed fixture with
undeclared missing player data. Declared provider gaps remain visible in snapshot
coverage. `coverage_gaps` is an acceptable retrieval state: missing trophy evidence
does not stop earlier, independently verified weeks.

```sh
python manage.py replay_weekly_rankings --max-runtime 1800 --max-weeks 20
```

Each period is replayed chronologically at **Monday 06:00 UTC**, including summer
national-team matches assigned to that award period. A terminal partial-week cutoff
captures the retained-data boundary. The fixed plan is captured once, prioritizing
2024/25 and 2026/27, then the remaining seasons newest first. Scores accumulate only
matches through each cutoff; titles require existing verification and an award date
no later than that cutoff. Retained Elo/context is reused; a season with missing Elo
is prepared using the existing local rebuild, never a new formula.

Publication and score creation for one cutoff are atomic. Weeks without eligible
players produce no artificial ranking. Trophy/data-quality failures stay pending
with their exact reason; other weeks and periods can continue. Unchanged blocked
weeks are not expensively recalculated on each run; changed campaign/fixture/appearance
evidence permits a retry. An existing public snapshot is reused without modification.
Batch limits are checked between cutoffs, so a calculation already in progress can
extend beyond the soft runtime limit. Unexpected failures are recorded and fail the
service. There is no provider client in this worker.

Progress is stored in `ProviderSyncState(provider="api_football",
sync_key="weekly-ranking-replay")`: captured plan, per-week status/reason, snapshot
IDs, durations, counters and error. `pending_verification` is partial delivery, not
completion. The four known season-final blockers at scheduling time are 2017/18
(UCL), 2018/19 (Bundesliga), 2019/20 (Ligue 1), and 2025/26 (AFCON).

Install the tracked `deployment/ranked-weekly-replay.service` and `.timer` in
`/etc/systemd/system`, reload systemd, then enable the timer. It resumes hourly at
00:00, 01:00 and 11:00–23:00 UTC, avoiding the daily 03:00 archive service's eight-hour
window. Both commands share the `season_backfill` PostgreSQL advisory lock; a busy
archive defers replay. The replay is lower-priority and independent of the desktop
app. The existing archive timer, API budget and reserve are unchanged.

```sh
systemctl status ranked-weekly-replay.timer ranked-weekly-replay.service
journalctl -u ranked-weekly-replay.service --no-pager -n 30
```

The homepage, position lists and player page derive movement from real
public ranks. After replay begins, the baseline is the preceding same-formula weekly
snapshot, not a same-date formula revision or a daily simulation. Until a real
earlier cutoff exists, show `—` with an explanatory tooltip, not `NEW`. A player
absent from a real baseline is `NEW`; a changed award category is `NEW category`.
Existing frozen entry movement fields remain unchanged. Player charts use distinct
dated weekly cutoffs plus the selected terminal publication, removing coincident
formula-revision points and keeping the existing season-long SVG bounds.

This is a bounded archive reconstruction, not a replacement for the ongoing weekly
publisher. The final public cutoff stays selected while earlier history fills in.
CPU time and storage grow with the number of weeks/players; scheduling does not
mean the whole archive has already been replayed.
