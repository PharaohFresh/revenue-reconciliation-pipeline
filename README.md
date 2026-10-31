# Revenue Reconciliation Pipeline

A runnable payment-to-booking pipeline that keeps revenue, delivery acceptance and attribution separate. It reconciles successive cumulative exports, groups installments into stable bookings, preserves the original conversion time and resumes partial delivery without duplicating successful requests.

**Author:** [Amir Ebrahim](https://www.linkedin.com/in/amirebrahim/) · Senior Analytics Engineer

For a booking business, a deposit and a later balance belong to one commercial outcome. Treating every payment row as a new conversion inflates booking counts. Re-uploading an old booking without an explicit value update can also leave its reported value at the deposit. This project makes those grains and state transitions visible.

All identities, payments, consent references and click identifiers are invented. Delivery uses a durable local fake API. No advertising account, customer records, cloud credentials or external requests are involved.

## Start here

| Reader | Useful starting point |
|---|---|
| Recruiter or hiring manager | [Generated demo report](examples/demo-report.json) and the three-export scenario below |
| Analytics engineer | [Booking and payment contracts](docs/data-contracts.md), [planning logic](src/revenue_reconciliation/model.py), and exact cent-based totals |
| Data/platform engineer | [Checkpoint and delivery workflow](src/revenue_reconciliation/workflow.py), [fake API](src/revenue_reconciliation/platform.py), and [failure tests](tests/test_reconciliation.py) |

## The three-export scenario

| Input | Source changes | Pipeline behavior |
|---|---|---|
| Deposits | Two payment events, two bookings | Plan two creates. One simulated API rejection leaves a partial result; resume only the pending request. |
| Later balances | Five cumulative events, three bookings | Restate the first two booking values while retaining their conversion times. Hold the new booking whose consent evidence is unknown. |
| Refund and returning customer | Seven cumulative events, four bookings | Hold the refund for a separate adjustment decision. The returning customer's new booking receives its own ID and conversion. |

Every identical export replay produces zero new delivery actions. A simulated response loss after acceptance is recovered with the same operation ID and independent readback. These are executable fixture outcomes, not claims about a live ad platform.

```mermaid
flowchart LR
    A[Cumulative payment CSV] --> B[Validate headers, IDs, cents and timestamps]
    B --> C[Compare immutable payment history]
    C --> D[Group by stable booking ID]
    D --> E[Evidence and refund holds]
    E --> F[Exact proposed creates and restatements]
    F --> G[Explicit local approval]
    G --> H[Deliver to fake API with bounded retry]
    H --> I[Independent record readback]
    I --> J[Checkpoint successful operations]
    J --> K[Preserve source and delivery evidence separately]
```

## Run it

Python 3.12 or newer is sufficient. The runtime uses only the standard library.

```text
python -m pip install -e ".[dev]"
python -m revenue_reconciliation demo --output artifacts/demo
python -m pytest -q
```

Run from the repository root in PowerShell, Bash or a terminal on macOS/Linux. Use a virtual environment. The demonstration output directory must be new; rerunning into an existing evidence directory is blocked.

Expected output contains four execution reports: a partial deposit batch, its successful resume, the balances and the refund/returning-customer export. The last report is complete **for eligible delivery operations**, with the refund and missing-consent holds still visible. Completion does not mean every booking was delivered or attributed.

### Inspect the outputs

- `demo-report.json`: scenario results and final ledger.
- `ledger.json`: current payment history, accepted booking state and checkpoint.
- `fake-platform.json`: independent local API records, operation IDs, attempts and unique effects.
- `evidence/<plan hash>/`: immutable plan, approval, previous ledger and separate execution-attempt receipts.

In the final synthetic scenario, source service value is 172,500 cents, net service cash is 162,500 cents, and accepted conversion value is 152,500 cents. Those are different measures: an unknown-consent booking is held, and the refunded booking's accepted value has not been silently retracted. The [tests](tests/test_reconciliation.py) assert these totals and boundaries.

## Plan, review and apply one export

```text
python -m revenue_reconciliation plan --export src/revenue_reconciliation/fixtures/01_deposits.csv --metadata src/revenue_reconciliation/fixtures/01_deposits.json --state-dir artifacts/manual-state --output artifacts/review/plan.json
python -m revenue_reconciliation approve --plan artifacts/review/plan.json --export src/revenue_reconciliation/fixtures/01_deposits.csv --metadata src/revenue_reconciliation/fixtures/01_deposits.json --actor demo-reviewer --output artifacts/review/approval.json
python -m revenue_reconciliation apply --plan artifacts/review/plan.json --approval artifacts/review/approval.json --export src/revenue_reconciliation/fixtures/01_deposits.csv --metadata src/revenue_reconciliation/fixtures/01_deposits.json --state-dir artifacts/manual-state
```

Planning reads inputs and proposes changes without updating working state. Read the full action/hold set before approving it. Apply rebuilds the plan against the current source bytes and checks the exact approval hash. Edited files, a changed ledger or an altered action set invalidate that approval.

The local approver label is an attestation, not authenticated production identity. The sample demo uses `synthetic-demo-reviewer` to make the simulated approval explicit. The executable target is the local fake API; no production integration is implemented.

Exit code `0` means the requested operation completed. Code `1` means input, authority, state or delivery prevented completion; a partial apply also returns `1`. Argument errors use code `2`. The complete demonstration returns `0` because it reproduces and resolves its intended partial failure while retaining its manual holds.

## Engineering decisions

- **Payment grain differs from booking grain.** Payment IDs identify immutable events; booking IDs identify commercial outcomes. Returning customers can have multiple bookings. The demonstration requires explicit booking links rather than guessing from consecutive invoice numbers.
- **Cumulative means cumulative.** A later export must include previously recorded events. Missing old IDs, changed amounts/identities, duplicate IDs within one export or a regressed watermark fail before application. They are not treated as refunds or a reset to zero.
- **Amounts are integer service cents.** The field excludes tax and tips by contract. Fractional cents and negative raw amounts are rejected. Refund kind supplies the sign for net service cash; attribution adjustments remain a separate decision.
- **Time is sourced.** Payment timestamps use ISO seconds, an explicit UTC offset and at most six fractional digits. Unsupported precision fails rather than silently rounding. Supported instants normalize to UTC. One unambiguous retainer establishes the initial conversion time; subsequent restatements preserve the accepted time.
- **Evidence is scoped to the booking.** A consent claim needs a source reference and a matching customer ID; customer IDs, source references and click candidates must be nonblank strings. Missing/denied consent, multiple click candidates and one click claimed by multiple bookings are held. Accepted click ownership survives later exports that omit that evidence. Restatements preserve the accepted click and its source; changing attribution evidence needs a separate correction decision. A missing click ID is not invented.
- **Accept only after readback.** A successful reply is insufficient. Value, time and customer identity must match the independent fake-platform record before the operation enters the accepted ledger.
- **Recover partial batches.** Each exact action has a stable operation ID. Completed operations are checkpointed. A lost response can be recovered from the fake API's matching idempotency record without repeating its effect. Retries are bounded; permanent rejection and readback mismatch remain partial failures. No command closes out a plan that cannot complete; that terminal reconciliation is a manual decision.
- **Preserve uncertainty.** API acceptance never sets matching or reported-attribution fields to true. Refund holds remain visible, even if the eligible action set completes.

## Tests and evidence

Tests cover the three successive exports, identical replay, returning customers, stable conversion timestamps, exact financial totals, partial-batch resume, bounded retries, lost responses for both creates and restatements, corrupt readback, altered approvals/sources, checkpoint drift, omitted/changed/duplicate payments, invalid cents/timestamps, watermarks, consent evidence, identity conflict and ambiguous/shared clicks.

[CI](.github/workflows/ci.yml) runs the offline suite and demo on Python 3.12 and 3.14 without secrets. [Examples](examples/) retains actual command-generated output from the synthetic fixture.

## Limits and production adaptation

This is an original reference implementation on a small, fixed dataset. The fake API explicitly supports durable operation IDs and readback; a real platform's idempotency, adjustment windows, time semantics, rate limits and diagnostics may differ. SQLite or a transactional database, concurrent writers, live identity matching and live advertising APIs are not implemented.

The working ledger and fake-platform files assume a single writer. Each is replaced atomically, with immutable before/plan/attempt evidence retained. A production implementation needs transaction/concurrency control, a trusted approval store, durable retry queues and provider-specific reconciliation. A remote effect cannot always be rolled back; after an ambiguous response, verify it before sending a new action.

The consent and single-booking click-ownership rules are conservative fixture policies, not legal interpretations or universal advertising-platform rules. A real click can precede multiple purchases; this demo holds reused evidence for review rather than deciding their attribution. No real customer hashes, click IDs, payment files or account identifiers belong in public examples.

## Repository map

```text
src/revenue_reconciliation/
  model.py        input contracts, booking grouping, evidence holds and exact plans
  platform.py     durable local fake API, idempotency and readback
  workflow.py     bounded execution, checkpoint recovery and immutable evidence
  cli.py          demo, plan, approve and apply commands
  fixtures/       three invented cumulative exports and booking evidence
tests/            input, accounting, recovery and command-flow checks
examples/         generated public proof
docs/             data contracts and delivery-state design
```

Inspired by payment intake and business-outcome measurement workflows. This public implementation and dataset were created for the demonstration; no private business records or source code are included. [MIT license](LICENSE).

Related work: [analytics warehouse](https://github.com/PharaohFresh/analytics-engineering-portfolio) · [governed delivery](https://github.com/PharaohFresh/agentic-analytics-delivery) · [query optimizer](https://github.com/PharaohFresh/governed-query-optimizer).
