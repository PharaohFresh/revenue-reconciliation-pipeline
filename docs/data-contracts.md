# Payment and booking data contracts

## Payment events

The cumulative CSV has exactly six columns:

| Field | Contract |
|---|---|
| `payment_id` | Nonempty stable identifier; unique within the export; immutable after intake |
| `booking_id` | Nonempty commercial-outcome identity; multiple installments share it |
| `customer_id` | Invented stable customer identity; one booking cannot change customer |
| `kind` | `retainer`, `balance` or `refund` |
| `occurred_at` | `YYYY-MM-DDTHH:MM:SS[.ffffff]Z` or a whole-minute `+/-HH:MM` offset; zero to six fractional digits; normalized to UTC |
| `service_amount_cents` | Nonnegative integer service amount; excludes tax/tips |

Payment history is not replaced by a smaller export. Missing prior IDs and changed prior records fail. Equivalent timezone representations of the same supported instant normalize consistently. More than six fractional timestamp digits and fractional offset seconds are rejected, so precision loss cannot hide a historical change. Fractional cents are not rounded silently. Empty exports do not mean the business earned zero.

The metadata sidecar supplies `export_as_of` and a `bookings` mapping. The watermark cannot regress and cannot precede any included payment. Each booking evidence record names the source customer, consent state/source, optional synthetic click candidates and their source. The customer ID and each click candidate must be nonblank strings. So must the consent source when a consent state is claimed, and the click source when click candidates are present. Whitespace, objects and numbers are rejected. Evidence conflicting with payment identity fails.

## Measures

- Service value: retainer plus balance amounts, grouped by booking.
- Refund service cents: sum of explicitly identified refund events.
- Net service cash: service value minus refund service cents.
- Accepted conversion value: latest value independently read back from the fake API for accepted bookings.

These measures need not agree. Unknown evidence can hold delivery, and a refund can change cash before any advertising adjustment has been decided. The example keeps the distinction visible rather than manufacturing a reconciled attribution result.

## Delivery actions

A new eligible booking produces a create. A later eligible balance produces a restatement if its service value differs from the accepted value. The original accepted conversion instant remains unchanged. Equal accepted values produce no new action. A returning customer with a new booking ID is a separate outcome.

Refunded bookings, missing/ambiguous retainers, denied/unknown consent, ambiguous clicks and cross-booking click collisions are held. This fixture policy deliberately does not infer consent from payment or submission. It also does not claim that absent click IDs prevent all possible matching: accepted identity delivery and provider matching remain separate states.

Accepted records retain click identity and its original source. Collision checks combine current evidence with this accepted ownership, including when later metadata omits a previously accepted click. Value restatements keep that accepted provenance; a different proposed click is held for an explicit correction. This single-booking ownership rule is a conservative demo policy, not a claim that real platforms prohibit multiple purchases after one click. A production adapter needs a separate documented correction path where the provider supports it.
