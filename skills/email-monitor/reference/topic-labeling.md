# Step 5, Topic labeling: decide what a message is about, or refuse

Add-only and opt-in. The kernel never removes a label and never touches `\Inbox`, so the worst
case of a wrong verdict is one extra word on one message. That asymmetry is what lets the rest of
this be aggressive about refusing.

**Omission over commission.** A missing label costs a manual glance. A wrong label costs trust in
every label, which is the defect this design exists to prevent. Every gate below is allowed to
return nothing, and an empty answer is stated in the prompt as valid and often correct.

## Why an evidence gate rather than a confidence score

A model's self-reported confidence is least reliable exactly where it has the least information.
Requiring it to quote a span that actually occurs in the input is far harder to fake than a number,
and it is checkable by a string operation instead of by trusting the model about itself.

`evidence_holds()` normalizes case and collapses whitespace on both sides, then asks whether the
quoted span occurs literally in `From + Subject`. Nothing else counts. Dropping is silent to the
mailbox and never silent to the log: every dropped label carries its `drop_reason`.

## Judgement inputs

`From`, `Subject`, `Date`, `List-Id`. **Never the body.** This bounds how wrong a verdict can be:
whatever the kernel decides, a human can re-derive from the same two visible lines.

## Three-step narrowing (`em_topic.judge`)

**1. Deterministic pre-gate (`pregate`).** Look the sender up in the map: `by_address`, then
`by_domain`, then `by_list_id`. **The most specific statement about a sender wins**, so a per-address
entry overrides its own domain. Returns `None` rather than `[]` when nothing matches, so a caller
cannot confuse "mapped to nothing" with "not mapped".

The pre-gate contributes **SOURCE labels only** (`who sent this`). A TYPE label (`what kind of mail
is this`, e.g. a receipt) is a property of the individual message, so a sender-keyed rule can never
settle it: a shop sends both order confirmations and marketing from one address. Type labels
therefore always reach the model, even on a pre-gate hit.

Pre-gate hits are held to the same two checks as model output: a label outside this account's
allowed set is dropped, and the mapped evidence must satisfy `evidence_holds`.

**2. Ask the model, as small a question as possible.** When the source is already settled, the
prompt says so and asks only for the type labels, which narrows the surface on which it can be
wrong. Allowed labels are listed byte for byte; anything not on the list is discarded. The prompt
forbids reasoning from sender habit ("this sender is usually X") and demands a verbatim span per
label.

**3. Verify (`verify_labels`).** Partition proposals into kept and dropped by `evidence_holds`.
A label the pre-gate already settled is not re-added.

## Three states, because two of them write nothing for different reasons

| state | meaning | mailbox | operator |
|---|---|---|---|
| `decided` | at least one label survived verification | labels added | nothing to do |
| `unsure` | the model answered, nothing survived the gate | nothing written | normal; a recurring pattern means the standard needs an entry |
| `failed` | the call itself broke (transport, unparseable reply) | nothing written | **investigate**; a tick full of `failed` looks identical to a quiet tick in the counters |

Collapsing these into a boolean is the mistake this table exists to prevent.

**A settled source label survives a broken transport.** If the map already established the source
and the model is unreachable, the verdict is still `decided` with that label and a reason naming the
failure. Losing what a deterministic rule already proved, because an unrelated call timed out, would
be strictly worse than not calling at all.

## Writeback and the label-creation hazard

`gmail-imap-label.py --add` **creates a label that does not exist**. That single fact drives the
config discipline:

- The allowed-label set is **per account**, and every name in it must exist in the mailbox.
  A stale name does not error, it silently resurrects the old label or splits one into two.
- The sender map is **shared across accounts**. A mapped label missing from *this* account's allowed
  set is dropped by design and is not a defect. A mapped label missing from *every* account's allowed
  set is dead: it can never pass the intersection anywhere, so those senders silently go unlabelled
  forever. Check the union, not the per-account set.
- Renaming a label is the dangerous operation, because **neither order is safe**: rename the mailbox
  first and the next tick recreates the old name; rename the config first and the next tick builds a
  second label under the new one. Pause topic labeling, confirm `topic_labeling=DISABLED` in the
  tick's own log, change both sides, then re-enable.
- A rename's read-back inside the same IMAP session proves nothing. Some accounts take **minutes**
  to converge, during which the paths and counts IMAP reports are not trustworthy and look exactly
  like a revert. Wait, then read back on a fresh connection.

## Gmail categories are not labels

`CATEGORY_SOCIAL` / `CATEGORY_PROMOTIONS` / `CATEGORY_UPDATES` are inbox tabs, mutually exclusive,
and **not writable over IMAP** at all. Six spellings were tried; the server answered OK to every one
and none changed category membership, creating ordinary labels with those names instead. A `STORE`
return code proves nothing here. Anything that belongs in a category is delivered by a Gmail filter,
not by this kernel, and the kernel must not ship a label that duplicates one.

## Configuration lives elsewhere

`rules/taxonomy.md` (the only judgement standard), `rules/sender_map.json`, and `rules/labels.json`
are DATA and live only in the private companion config. `labels.json` is keyed by the **account slug
from `registry.json`**, not by any shorter nickname: `load_config` does a plain lookup and returns
`None` on a miss, which makes the whole capability inert while the flag still reads enabled. The
tick logs that case explicitly, because the top-level `topic_labeling=` line cannot tell "off" from
"on but unconfigured" on its own.
