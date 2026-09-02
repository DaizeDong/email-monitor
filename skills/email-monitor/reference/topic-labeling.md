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

## Repairing a corpus in bulk: the two ways it lies to you

Fixing a bad map entry does not fix the mail it already mislabelled, so a rule change is normally
followed by a bulk move through `gmail-imap-label.py`. Both times that was done on 2026-09-01 the
tool reported success while doing less than it claimed.

**`matched 0` exits 0.** Every move in a 72-message batch returned rc=0 and 14 of them changed
nothing; the count only surfaced because the residual was checked afterwards and came back higher
than the verdicts predicted. `rc` answers "did the tool run", never "did it find anything". A caller
that does not parse the `matched N` line cannot tell a completed move from a no-op, which is the
same clean-versus-never-looked confusion the exit codes elsewhere in this skill exist to prevent.

**The listing truncates the subject at ~54 characters, mid-word.** So a query built from that
listing asks Gmail for `subject:"...and Course Assign"`, phrase search tokenizes, and `Assign` never
matches `Assignments`. Drop the trailing partial token before querying. This is also why a subject
query is a poor message identifier in general: it is not unique either, and duplicates in the same
batch will move together on the first query and report `matched 0` on the second, which looks
identical to the truncation failure and is harmless. Distinguish them by the residual count, not by
the per-move output.

The check that catches both: after the batch, count what still carries the old label and compare it
to the number of verdicts that said "keep". Equal means every intended move landed and nothing was
swept in by accident. That number disagreeing in EITHER direction is a real defect -- lower means a
query over-matched and moved mail nobody judged, higher means moves silently did nothing.

## Re-judging a mixed sender is not the same as retargeting it

Some addresses are not a topic. One department list (`announce@example.edu`) carried HR onboarding
forms, seminar invitations, job ads, free t-shirt notices and visa paperwork through a single
address, and re-judging all 84 of its messages moved 74 of them to six different labels. No entry in the sender map could have been right, so the
entry was removed and the mail falls through to per-message judgement, which the map's own note
already names as the safe direction for anything the audit cannot settle. Two shapes to recognise:
a list that serves a whole organisation, and any individual human, who by definition writes about
more than one thing.

When re-judging a corpus, require a quorum of parseable verdicts before writing anything. The first
attempt got 6 valid verdicts out of 26 and aborted on a 90% bar rather than applying a quarter of a
plan; smaller batches with a retry then returned 26 of 26. A partial apply here is worse than no
apply, because the next run cannot tell which messages it already handled.

## One review pass is not enough, and the reason is structural

After the first round of map fixes, two labels that had sampled 7 wrong out of 20 came back at 0,
and the corpus the mail moved into sampled clean. The fix held. But the label with the worst rate
came back at the SAME rate, with entirely different senders behind it.

That is not the fix failing. It is what a sample does: the loudest sender crowds the sample, and
only once it is gone do the next ones become visible. So a label that is still red after a fix has
to be read as new evidence, not as a failed repair, and the senders behind it have to be listed
again rather than assumed to be the ones already dealt with. A single pass would have left the
second set in place and reported the label as unfixable.

Re-run the review on exactly the labels touched, plus the labels the mail moved INTO. The second is
easy to skip and is the one that catches a bad move: a corpus that was clean before a bulk insert
and is dirty after it was polluted by that insert.
