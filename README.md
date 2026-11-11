# Revenue Reconciliation Pipeline

A reference pipeline that turns deposits, balance payments and refunds into consistent booking-level records. It preserves identity across exports, resumes partial delivery without duplicate operations, and keeps cash received separate from conversion values accepted by a simulated API. All records are synthetic.

**Author:** [Amir Ebrahim](https://www.linkedin.com/in/amirebrahim/) · Senior Analytics Engineer

For a booking business, a deposit and a later balance belong to one commercial outcome. Treating every payment row as a new conversion inflates booking counts. Re-uploading an old booking without an explicit value update can also leave its reported value at the deposit. This project makes those grains and state transitions visible.

Delivery uses a durable local fake API. No advertising account, customer records, cloud credentials or external requests are involved.

## Start here

| Reader | Useful starting point |
|---|---|
| Recruiter or hiring manager | The [three quantities and export timeline](#the-three-export-scenario) below; the [generated demo report](examples/demo-report.json) is the raw evidence |
| Analytics engineer | [Booking and payment contracts](docs/data-contracts.md), [planning logic](src/revenue_reconciliation/model.py), and exact cent-based totals |
| Data/platform engineer | [Checkpoint and delivery workflow](src/revenue_reconciliation/workflow.py), [fake API](src/revenue_reconciliation/platform.py), and [failure tests](tests/test_reconciliation.py) |

## The three-export scenario

After the third export, the same four bookings add up to three different totals. Reporting any one of them as "revenue" hides the other two.

| Synthetic final quantity | Amount | Meaning |
|---|---|---|
| Source service value | 172,500 cents ($1,725) | Service payments received across four bookings, before refunds |
| Net service cash | 162,500 cents ($1,625) | The same payments after the 10,000-cent ($100) refund |
| Accepted conversion value | 152,500 cents ($1,525) | Value the simulated API accepted for eligible bookings |

| Export | Payments → bookings | What the pipeline did | Accepted value after | Still held |
|---|---|---|---|---|
| 1. Deposits | 2 → 2 | Planned two creates. A simulated platform rejection (cleared before the resume) left the batch partial at 1 of 2; the resume sent only the pending create. | 25,000, then 55,000 | None |
| 2. Later balances | 5 → 3 | Restated the first two bookings and kept their original conversion times. One restatement recovered from a simulated lost response. | 134,500 | One new booking: consent evidence missing |
| 3. Refund and returning customer | 7 → 4 | Created the returning customer's new booking under its own ID. | 152,500 | The refund (needs an adjustment decision) and the consent hold |

A run reports `complete` once its eligible actions finish, even while refund and consent cases stay held. Every identical export replay produces zero new delivery actions, and a simulated response loss after acceptance is recovered with the same operation ID and independent readback.

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

Python 3.12 or newer. The runtime uses only the standard library.

```text
python -m pip install -e ".[dev]"
python -m revenue_reconciliation demo --output artifacts/demo
python -m pytest -q
```

Use a virtual environment. The demonstration output directory must be new.

Expected output contains four execution reports: a partial deposit batch, its successful resume, the balances and the refund/returning-customer export. The last report is complete for eligible delivery operations, with the refund and missing-consent holds still visible.

### Inspect the outputs

- `demo-report.json`: scenario results and final ledger.
- `ledger.json`: current payment history, accepted booking state and checkpoint.
- `fake-platform.json`: independent local API records, operation IDs, attempts and unique effects.
- `evidence/<plan hash>/`: immutable plan, approval, previous ledger and separate execution-attempt receipts.

The [tests](tests/test_reconciliation.py) assert the three totals above. They differ because an unknown-consent booking is held and the refunded booking's accepted value is not silently retracted.

## Plan, review and apply one export

```text
python -m revenue_reconciliation plan --export src/revenue_reconciliation/fixtures/01_deposits.csv --metadata src/revenue_reconciliation/fixtures/01_deposits.json --state-dir artifacts/manual-state --output artifacts/review/plan.json
python -m revenue_reconciliation approve --plan artifacts/review/plan.json --export src/revenue_reconciliation/fixtures/01_deposits.csv --metadata src/revenue_reconciliation/fixtures/01_deposits.json --actor demo-reviewer --output artifacts/review/approval.json
python -m revenue_reconciliation apply --plan artifacts/review/plan.json --approval artifacts/review/approval.json --export src/revenue_reconciliation/fixtures/01_deposits.csv --metadata src/revenue_reconciliation/fixtures/01_deposits.json --state-dir artifacts/manual-state
```

Planning reads inputs and proposes changes without updating working state. Apply rebuilds the plan against the current source bytes and checks the exact approval hash; edited files, a changed ledger or an altered action set invalidate that approval. The approver label is a local attestation, and the demo uses `synthetic-demo-reviewer`.

## Engineering decisions

- **Payment grain differs from booking grain.** Payment IDs identify immutable events; booking IDs identify commercial outcomes. Returning customers can have multiple bookings, so the demonstration requires explicit booking links rather than guessing from consecutive invoice numbers.
- **Cumulative means cumulative.** A later export must include previously recorded events. Missing old IDs, changed amounts/identities, duplicate IDs within one export or a regressed watermark fail before application.
- **Amounts are integer service cents.** The field excludes tax and tips by contract. Fractional cents and negative raw amounts are rejected. Refund kind supplies the sign for net service cash; attribution adjustments remain a separate decision.
- **Time is sourced.** Payment timestamps use ISO seconds, an explicit UTC offset and at most six fractional digits, and normalize to UTC. One unambiguous retainer establishes the initial conversion time; subsequent restatements preserve the accepted time.
- **Evidence is scoped to the booking.** A consent claim needs a source reference and a matching customer ID. Missing/denied consent, multiple click candidates and one click claimed by multiple bookings are held. Accepted click ownership survives later exports that omit that evidence; changing attribution evidence needs a separate correction decision.
- **Accept only after readback.** Value, time and customer identity must match the independent fake-platform record before the operation enters the accepted ledger.
- **Recover partial batches.** Each exact action has a stable operation ID, and completed operations are checkpointed. A lost response is recovered from the fake API's matching idempotency record without repeating its effect. Retries are bounded; permanent rejection and readback mismatch remain partial failures.
- **Preserve uncertainty.** API acceptance never sets matching or reported-attribution fields to true. Refund holds remain visible, even if the eligible action set completes.

## Tests and evidence

Tests cover the three successive exports, identical replay, returning customers, stable conversion timestamps, exact financial totals, partial-batch resume, bounded retries, lost responses for both creates and restatements, corrupt readback, altered approvals/sources, checkpoint drift, omitted/changed/duplicate payments, invalid cents/timestamps, watermarks, consent evidence, identity conflict and ambiguous/shared clicks.

[CI](.github/workflows/ci.yml) runs the offline suite and demo on Python 3.12 and 3.14 without secrets. [Examples](examples/) retains actual command-generated output from the synthetic fixture.

## Production adaptation

The fake API supports durable operation IDs and readback. A real platform needs transaction and concurrency control, a trusted approval store, durable retry queues and provider-specific reconciliation; after an ambiguous response, verify the remote effect before sending a new action. [Delivery state and recovery](docs/recovery.md) covers checkpoints and partial outcomes.

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
docs/             data contracts, delivery state and recovery
```

Inspired by payment intake and business-outcome measurement workflows. This public implementation and dataset were created for the demonstration; no private business records or source code are included. [MIT license](LICENSE).

Related work: [analytics warehouse](https://github.com/PharaohFresh/analytics-engineering-portfolio) · [governed delivery](https://github.com/PharaohFresh/agentic-analytics-delivery) · [query optimizer](https://github.com/PharaohFresh/governed-query-optimizer).
