# Step 5, Topic labeling: decide what a message is about, or refuse

Topic labeling is opt-in and add-only. The kernel never removes a label or changes `\Inbox`.
A wrong verdict can still reduce label accuracy, so every gate may return no label. The prompt
explicitly permits an empty answer when the input does not support a classification.

## Why an evidence gate rather than a confidence score

A self-reported confidence score does not provide independently checkable evidence. Requiring a
quoted input span permits deterministic verification that the supporting text exists. The check
establishes text presence; taxonomy interpretation still needs review.

`evidence_holds()` normalizes case and collapses whitespace on both sides, then asks whether the
quoted span occurs literally in `From + Subject`. Model proposals and mapped address/domain
evidence use this check. Deterministic `by_list_id` mappings have the separate identity check
described below. Dropped labels leave the mailbox unchanged and carry a `drop_reason` in the log.

## Judgement inputs

The kernel receives `From`, `Subject`, `Date` and `List-Id`; the body is excluded. `List-Id`
supports the deterministic sender map. The model prompt includes `From`, `Subject` and `Date`,
the private taxonomy, allowed labels and any settled source label. Model evidence must quote
`From` or `Subject`; `Date` supplies context but cannot establish evidence.

When `topic_labeling.enabled` is true and model judgment is needed, the heartbeat passes that
prompt to `llmcall.call(..., mode="judge")`. The installed routing policy may use external providers.
This header-only topic route is separate from default agent importance classification, which
includes body text. `runtime.local_only=true` rejects enabled topic models before reading mail.
A dry tick may still make the configured topic judgment call, but it does not apply labels or
persist topic retries. Use heuristic classification with topics disabled to avoid model transport.

## Three-step narrowing (`em_topic.judge`)

**1. Deterministic pre-gate (`pregate`).** Look the sender up in the map: `by_address`, then
`by_domain`, then `by_list_id`. **The most specific statement about a sender wins**, so a per-address
entry overrides its own domain. Returns `None` rather than `[]` when nothing matches, so a caller
cannot confuse "mapped to nothing" with "not mapped".

The pre-gate contributes **SOURCE labels only** (`who sent this`). A TYPE label (`what kind of mail
is this`, e.g. a receipt) is a property of the individual message, so a sender-keyed rule can never
settle it: a shop sends both order confirmations and marketing from one address. Type labels
therefore always reach the model, even on a pre-gate hit.

Every pre-gate label must belong to this account's allowed set. Address and domain mappings
must satisfy `evidence_holds`. A `by_list_id` mapping instead carries `source="map"` and
`evidence_header="list_id"`; its nonempty normalized evidence must equal the normalized identity
returned by `_list_identity(msg)`. Model proposals cannot use this List-Id exception.

**2. Ask the model, as small a question as possible.** When the source is already settled, the
prompt says so and asks only for the type labels, which narrows the surface on which it can be
wrong. Allowed labels are listed byte for byte; anything not on the list is discarded. The prompt
forbids reasoning from sender habit ("this sender is usually X") and demands a verbatim span per
label.

**3. Verify (`verify_labels`).** Partition proposals into kept and dropped using the header-span
check or the narrow mapped List-Id check above. Model proposals require `evidence_holds`.
A label the pre-gate already settled is not re-added.

## Three states, because two of them write nothing for different reasons

| state | meaning | mailbox | operator |
|---|---|---|---|
| `decided` | at least one label survived verification | labels added | nothing to do |
| `unsure` | the model answered, nothing survived the gate | nothing written | normal; a recurring pattern means the standard needs an entry |
| `failed` | the call itself broke (transport, unparseable reply) | nothing written | **investigate**; a tick full of `failed` looks identical to a quiet tick in the counters |

Keep the three states distinct even when two produce no mailbox write. If the map established a
source label and the model is unreachable, the verdict remains `decided` with that label and a
reason naming the failure. A model outage does not discard a verified deterministic source label.

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
  second label under the new one. Disable `topic_labeling.enabled` in the selected registry,
  confirm the active configuration before changing both sides, then re-enable.
- A rename's read-back inside the same IMAP session proves nothing. Some accounts take **minutes**
  to converge, during which the paths and counts IMAP reports are not trustworthy and look exactly
  like a revert. Wait, then read back on a fresh connection.

## Gmail categories are not labels

`CATEGORY_SOCIAL` / `CATEGORY_PROMOTIONS` / `CATEGORY_UPDATES` are inbox tabs, mutually exclusive,
and are not ordinary IMAP labels. Creating labels with those names does not establish
category membership. Use Gmail filters for category assignment and verify the resulting
membership separately; a successful STORE response is insufficient.

## Configuration lives elsewhere

`rules/taxonomy.md` (the only judgement standard), `rules/sender_map.json`, and `rules/labels.json`
are versioned DATA and live only in the verified PRIVATE companion config. Pending topic headers
and delivery receipts belong in that companion's action state as well. `labels.json` is keyed by the **account slug
from `registry.json`**, not by any shorter nickname: `load_config` does a plain lookup and returns
`None` on a miss, which makes the whole capability inert while the flag still reads enabled. The
tick records configuration failures; its result retains pending topic work until the settings can
be loaded. A configured label still needs a matching delivery receipt before its action is complete.

## Verify bulk changes by message identity

Correcting a sender-map entry affects future judgments. Existing labels need a separately
reviewed repair plan. Save the intended changes and prior label sets in PRIVATE DATA before
applying any change.

A helper can exit zero after matching no messages. Inspect its matched count and verify the
resulting label set for every intended message. Use stable message identifiers: subjects can
be truncated, tokenize differently in search, or match several messages. Do not infer a
completed mutation from process exit or a search phrase alone.

After the batch, compare the remaining source-label count with the planned keep count. A
higher count can reveal missed changes; a lower count can reveal unintended changes. Counts
are a cross-check, not a substitute for checking the identities and final label sets.

## Mixed senders need per-message judgment

An organization-wide list or individual sender can discuss several topics. When a sender
does not reliably identify one topic, remove the unconditional map entry and let messages
reach the evidence gate individually.

Require the repair plan's completeness threshold before writing anything. If too few
verdicts are valid, stop and retry smaller batches. Record unresolved messages explicitly
so a later run can distinguish incomplete planning from completed application.

## Review both source and destination labels

Sampling can hide less frequent errors behind a dominant sender. After correcting a rule,
review the affected source labels again and inspect the labels that received moved messages.
A new set of findings needs its own evidence; do not assume it has the same cause as the
previous sample. A clean source sample alone does not validate the destination.

## Audit the decisions the kernel made

Exclude self-sent messages from this review. Thread-level operations can attach topic labels
to outgoing replies, so the presence of a label does not establish that the incoming-mail
kernel assigned it. Keep the audit population aligned with the component being evaluated.
Changing the sample does not authorize removing labels from the excluded messages.

## Resolve taxonomy ambiguity before moving mail

A finding supported by a specific label definition is evidence about the message. A finding
supported only by a general tie-break rule can instead reveal an underspecified taxonomy.
Resolve that ambiguity in the PRIVATE taxonomy before applying changes; otherwise successive
repairs can move the same messages back and forth.
