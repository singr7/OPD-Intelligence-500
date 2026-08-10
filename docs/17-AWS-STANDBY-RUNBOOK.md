# 17 — Controlled AWS standby promotion and failback

The Omen and AWS databases are never simultaneous writers. DNS failover is manual.
Use only non-PHI identifiers in the drill record.

## Fixed environments

- Omen: `https://omen.opd.radpretation.ai`
- AWS: `https://opd-cloud.radpretation.ai`
- stable Android/pairing alias: `https://opd.radpretation.ai`

**What AWS is running, and from which directory, is recorded in docs/18 §0** —
the deploy ledger. Read it before a drill: a promotion onto a box several
releases behind Omen is a promotion onto a different schema. Two things that bite
on the live host and are documented there in full: `/opt/opd/current` is a
symlink and there is **more than one checkout** under `/opt/opd/source` (derive
the path with `readlink -f /opt/opd/current`, never type it), and neither the
other checkout nor the previous release's images may be deleted, because
`rollback.sh` needs both.

## Before the first drill

1. Apply `infra/` and record the Terraform output. Confirm the security group has
   only 80/443 inbound and the instance is available through SSM without port 22.
2. On AWS, run `fetch-secrets.sh`, `bootstrap.sh`, an immutable `deploy.sh <sha>`,
   `enable-tls.sh`, and `install-operations.sh`.
3. On Omen, install `deploy/omen/opd-cloud-backup.{service,timer}`, run the service
   once, and restore that backup into an isolated database before enabling the timer.
4. Copy `deploy/aws/drill-record.example.json` outside Git for the live record.

## Omen to AWS

1. Complete one known intake on Omen and record only its non-PHI identifier.
2. Quiesce Omen writes using its maintenance window, stop write-producing workers,
   and prove a new write is rejected. Record `quiesced_at`.
3. Run `deploy/omen/cloud-backup.sh`; record its ID and cutoff timestamp.
4. On AWS run `restore.sh <backup-id>`, then `verify-restore.sh <backup-id>`.
5. Confirm AWS reports `default_transaction_read_only=on`; confirm Omen is still
   quiesced. Run `promote.sh` on AWS.
6. Switch the stable DNS or Android pairing manually. Do not configure automatic
   DNS health failover.
7. Pass public API, nginx, WebSocket, download, kiosk, and one cloud-profile voice
   check. Keep Omen read-only.
8. Create a clearly labelled post-cutoff intake on the old side before its final
   quiesce if the drill design permits it, and prove it is absent from the restored
   AWS backup. Never imply that backup replication captured it.

## AWS to Omen

Repeat the same direction in reverse: quiesce AWS with `quiesce.sh`, take the
on-demand backup, restore and verify on a stopped/read-only Omen database, promote
Omen, switch the stable alias, pass health/kiosk checks, and keep AWS read-only.

Finalize the record:

```bash
deploy/aws/drill-report.py live-record.json final-report.json
```

The report computes actual RPO from backup cutoff to quiesce and actual RTO from
quiesce to public health. It records a missed 15-minute/30-minute target honestly.
