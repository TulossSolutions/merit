# Production job alerts

Archive import and weekly replay send plain-language incident emails to
`merit@tuloss.com` when a native job fails, a ranking publication is rejected,
or a match is quarantined because a playing participant has provider ID 0.
Emails explain the issue and its impact. No API calls are made by the alert command.

The native services enable `MERIT_PRODUCTION_ALERTS=True` and call
`notify_production_job` from `ExecStopPost`, including after timeouts.
Local development remains disabled. Mail defaults to the production host's
existing Postfix transport on localhost:25, from `Merit <merit@tuloss.com>`.
Standard Django SMTP environment settings and `MERIT_ALERT_EMAIL` can override these.

Persistent receipts in `ProviderSyncState` prevent duplicate emails for the same
incident. A job failing again after a successful run is a new incident.
Rejected fixtures and weeks are identified individually. Transport failures
remain retryable and are logged; they do not replace the original job result.
SMTP acceptance is not proof of inbox delivery.

An unused ID-0 squad row is discarded without creating a player. If an ID-0
participant played, the entire fixture is excluded from scoring, its original
payload and review reason retained, and other fixture imports continue.
Duplicate nonzero canonical identities still fail validation.
