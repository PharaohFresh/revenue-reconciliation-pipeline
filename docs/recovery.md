# Delivery state and recovery

## Content binding

A plan embeds the initial ledger, proposed event state, exact action set, holds and both input-file hashes. Apply reconstructs it from the supplied export/metadata and initial ledger before checking approval. A changed file or action set requires a new plan and approval.

Approval is a local-file attestation, not authenticated identity. The fake platform and working state run under one local process owner. This design illustrates reviewed-content binding; it is not a hardened production authorization service.

## Checkpoints

```text
planned -> approved -> intake recorded -> delivery/readback -> checkpoint
                                         |
                                         +-> partial failure -> exact-plan resume
```

Immutable before-state/plan/approval files are saved before execution. Current payment state is recorded separately from accepted delivery. An operation becomes complete only after its exact platform record reads back correctly. The expected working ledger is reconstructed from the approved plan and completed operation IDs; unrelated state drift blocks resume.

A partial plan must complete or be explicitly reconciled before planning a new export. This avoids superseding an uncertain create with a different value. A new source version cannot silently bypass a failed action.

Resuming with `apply` is the only implemented path. No command performs terminal reconciliation: closing out a plan that cannot complete, for example after a permanent rejection, is a separate manual decision outside this reference.

## Response loss

The fake API stores the operation's effect and acknowledgement under its operation ID before it can inject a response-loss failure. Retry uses the same ID and returns the stored acknowledgement. Readback still verifies value, time, customer and retained click provenance.

For a restatement whose response was lost, the remote record may already contain the new value while the local ledger contains the old one. Resume permits that difference only when the exact pending operation exists in the fake API's idempotency map and the approved new record reads back correctly. An unrelated remote edit blocks execution.

## Partial and complete outcomes

Permanent rejection and corrupt readback do not count as success. The batch can retain other successful operations, with remaining failures explicit. `complete` means the plan's eligible action set finished; refund/evidence holds can remain. Each execution attempt has its own preserved receipt.

A real platform might not support the same idempotency contract or immediate readback. Adapt only after verifying its documented behavior; do not infer duplicate protection or attribution from an accepted response.
